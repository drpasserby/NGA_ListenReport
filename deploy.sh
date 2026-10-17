#!/usr/bin/env bash
# =============================================================================
#  NGA 举报监听 —— Linux 一键部署脚本
# -----------------------------------------------------------------------------
#  用法（在项目根目录执行）：
#    sudo bash deploy.sh                一键部署（自动装 Python/Node/PM2/依赖）
#    sudo bash deploy.sh --cn           国内网络加速（pip / npm 走国内镜像）
#    sudo bash deploy.sh -y             全程不询问，用默认值
#    sudo bash deploy.sh --no-startup   不设置开机自启
#    sudo bash deploy.sh --name myapp   自定义 PM2 进程名
#
#  日常运维：
#    bash deploy.sh status              查看 PM2 状态
#    bash deploy.sh logs [行数]         查看日志（默认 50 行）
#    bash deploy.sh restart             重启
#    bash deploy.sh stop / start        停止 / 启动
#    bash deploy.sh update              拉取最新代码 + 更新依赖 + 重启
#    bash deploy.sh uninstall           停止并移除 PM2 进程（不删代码和配置）
#
#  不克隆仓库，直接跑：
#    bash <(curl -fsSL https://raw.githubusercontent.com/drpasserby/NGA_ListenReport/main/deploy.sh)
#
#  脚本是幂等的：重复执行只会更新依赖并重启，不会重复安装。
# =============================================================================

if [ -z "${BASH_VERSION:-}" ]; then
    echo "请用 bash 运行本脚本： bash deploy.sh" >&2
    exit 1
fi

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# 常量与全局开关
# ---------------------------------------------------------------------------
ORIG_ARGS=("$@")
ORIGINAL_PATH="$PATH"
SELF="${BASH_SOURCE[0]}"
REPO_DIR="$(cd -- "$(dirname -- "$SELF")" >/dev/null 2>&1 && pwd || pwd)"

APP_NAME="${NGA_APP_NAME:-nga-listen-report}"
REPO_URL="${NGA_REPO_URL:-https://github.com/drpasserby/NGA_ListenReport.git}"
CLONE_DIR="${NGA_DIR:-$PWD/NGA_ListenReport}"

PYPI_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
NPM_MIRROR="https://registry.npmmirror.com"
NVM_MIRROR="https://npmmirror.com/mirrors/node"

NODE_MAJOR=20          # 安装哪个大版本的 Node.js
NODE_MIN_MAJOR=16      # 低于这个版本就重装（PM2 需要）

VENV_DIR="$REPO_DIR/venv"
VENV_PY="$VENV_DIR/bin/python"
CONFIG_FILE="$REPO_DIR/config.yaml"
CONFIG_TPL="$REPO_DIR/config.yaml.default"

USE_CN=0
ASSUME_YES=0
DO_STARTUP=1
CMD="install"
LOG_LINES=50

SUDO=""
PKG_MGR="unknown"
APT_UPDATED=0
BOOTSTRAP_NEEDED=0

# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RESET=$'\033[0m'; C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'
    C_YELLOW=$'\033[1;33m'; C_RED=$'\033[1;31m'; C_DIM=$'\033[2m'
else
    C_RESET=""; C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""
fi

log()  { printf '%s[部署]%s %s\n' "$C_BLUE"   "$C_RESET" "$*"; }
step() { printf '\n%s==> %s%s\n' "$C_BLUE"    "$*" "$C_RESET"; }
ok()   { printf '%s[完成]%s %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
warn() { printf '%s[注意]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf '%s[错误]%s %s\n' "$C_RED"    "$C_RESET" "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# 交互式确认，返回 0=是 / 1=否。-y 模式下一律返回 0
confirm() {
    local prompt="$1" default="${2:-y}" reply
    [ "$ASSUME_YES" -eq 1 ] && return 0
    [ -t 0 ] || return 0          # 非交互（如管道）按默认继续
    if [ "$default" = "y" ]; then prompt="$prompt [Y/n] "; else prompt="$prompt [y/N] "; fi
    read -r -p "$prompt" reply || reply=""
    reply="${reply:-$default}"
    case "$reply" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# ---------------------------------------------------------------------------
# 环境探测
# ---------------------------------------------------------------------------
detect_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        SUDO=""
    elif have sudo; then
        # 非交互（管道 / nohup / CI）时加 -n：立刻失败而不是卡在密码提示上
        if [ -t 0 ]; then SUDO="sudo"; else SUDO="sudo -n"; fi
    else
        SUDO=""
    fi
}

# 是否为"有效 root"（root 本人，或能通过 sudo 提权）
can_root() {
    [ "$(id -u)" -eq 0 ] && return 0
    [ -n "$SUDO" ] && return 0
    return 1
}

detect_pkg_mgr() {
    if   have apt-get; then PKG_MGR="apt"
    elif have dnf;     then PKG_MGR="dnf"
    elif have yum;     then PKG_MGR="yum"
    elif have apk;     then PKG_MGR="apk"
    elif have pacman;  then PKG_MGR="pacman"
    else PKG_MGR="unknown"
    fi
}

os_pretty() {
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        printf '%s' "${PRETTY_NAME:-${NAME:-Linux}}"
    else
        printf 'Linux'
    fi
}

install_packages() {
    [ "$#" -gt 0 ] || return 0
    log "安装系统软件包: $*"
    case "$PKG_MGR" in
        apt)
            if [ "$APT_UPDATED" -eq 0 ]; then
                $SUDO apt-get update -qq || warn "apt-get update 失败，继续尝试安装"
                APT_UPDATED=1
            fi
            $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
            ;;
        dnf)    $SUDO dnf install -y "$@" ;;
        yum)    $SUDO yum install -y "$@" ;;
        apk)    $SUDO apk add --no-cache "$@" ;;
        pacman) $SUDO pacman -Sy --noconfirm "$@" ;;
        *)      return 1 ;;
    esac
}

ensure_base_packages() {
    local need=()
    have python3 || need+=(python3)
    have curl    || need+=(curl)
    have git     || need+=(git)

    case "$PKG_MGR" in
        apt)
            have python3 || need+=(python3-venv python3-pip)
            [ "${#need[@]}" -gt 0 ] && need+=(ca-certificates)
            ;;
        apk)
            need+=(bash python3 py3-pip)
            ;;
    esac

    if [ "${#need[@]}" -gt 0 ]; then
        if can_root && [ "$PKG_MGR" != "unknown" ]; then
            # ${arr[@]+...} 是为了兼容 bash 4.2（CentOS 7）下 set -u + 空数组
            install_packages ${need[@]+"${need[@]}"} || warn "系统软件包安装失败，稍后会再尝试其它方式"
        else
            warn "缺少命令：${need[*]}，且当前用户无法自动安装，请先手动安装后重试"
        fi
    fi
    have python3 || die "找不到 python3，请先手动安装 Python 3.8+ 后重试"
    ok "基础环境就绪 ($(os_pretty) / python $(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:3])))'))"
}

node_major_version() {
    have node || { printf '0'; return; }
    node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/' || printf '0'
}

install_node_nodesource() {
    log "通过 NodeSource 安装 Node.js ${NODE_MAJOR}.x（官方源）"
    local setup="https://deb.nodesource.com/setup_${NODE_MAJOR}.x"
    have curl || return 1
    if [ -n "$SUDO" ]; then
        curl -fsSL "$setup" | $SUDO -E bash - || return 1
    else
        curl -fsSL "$setup" | bash - || return 1
    fi
    install_packages nodejs || return 1
}

install_node_nvm() {
    log "通过 nvm 安装 Node.js（用户级，无需 root）"
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ ! -s "$NVM_DIR/nvm.sh" ]; then
        have curl || return 1
        local nvm_ver="v0.40.1"
        curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/${nvm_ver}/install.sh" | bash || return 1
    fi
    # shellcheck disable=SC1090
    . "$NVM_DIR/nvm.sh" || return 1
    if [ "$USE_CN" -eq 1 ]; then
        NVM_NODEJS_ORG_MIRROR="$NVM_MIRROR" nvm install "$NODE_MAJOR" || return 1
    else
        nvm install "$NODE_MAJOR" || return 1
    fi
    nvm use "$NODE_MAJOR" >/dev/null 2>&1 || true
    hash -r
}

ensure_node() {
    step "检查 Node.js"
    local cur
    cur="$(node_major_version)"
    if [ "${cur:-0}" -ge "$NODE_MIN_MAJOR" ]; then
        ok "Node.js 已安装：$(node -v)  (npm $(npm -v 2>/dev/null || echo '?'))"
        return 0
    fi
    [ "${cur:-0}" -gt 0 ] && warn "Node.js 版本过低 (v$cur)，需要 >= v$NODE_MIN_MAJOR，将重新安装"

    if ! can_root || [ "$PKG_MGR" = "unknown" ]; then
        warn "无法用系统包管理器安装 Node.js，改用 nvm"
        install_node_nvm || die "Node.js 安装失败，请手动安装 Node.js >= $NODE_MIN_MAJOR 后重试"
    elif [ "$USE_CN" -eq 1 ]; then
        install_node_nvm || install_node_nodesource || die "Node.js 安装失败，请手动安装 Node.js >= $NODE_MIN_MAJOR 后重试"
    else
        install_node_nodesource || { warn "官方源安装失败，改用 nvm 重试"; install_node_nvm; } \
            || die "Node.js 安装失败，请手动安装 Node.js >= $NODE_MIN_MAJOR 后重试"
    fi

    hash -r
    have node || die "Node.js 安装后仍找不到 node 命令"
    ok "Node.js 安装完成：$(node -v)"
}

# 全局安装 npm 包。root 直接装；否则优先用可写的全局目录，
# 再退到 sudo，最后退到用户级 $HOME/.local（无需 root）
npm_global() {
    local pkg="$1"; shift
    local prefix user_prefix
    user_prefix="$HOME/.local"

    if [ "$(id -u)" -eq 0 ]; then
        npm install -g "$pkg" "$@" && return 0
        return 1
    fi

    prefix="$(npm config get prefix 2>/dev/null || echo /usr)"
    if [ -w "$prefix/lib/node_modules" ] 2>/dev/null || [ -w "$prefix" ] 2>/dev/null; then
        npm install -g "$pkg" "$@" && return 0
    fi

    if [ -n "$SUDO" ]; then
        $SUDO npm install -g "$pkg" "$@" && return 0
    fi

    warn "npm 全局目录（$prefix）不可写，改为用户级安装到 $user_prefix"
    mkdir -p "$user_prefix"
    npm install -g "$pkg" --prefix "$user_prefix" "$@" || return 1
    export PATH="$user_prefix/bin:$PATH"
    hash -r
    return 0
}

PM2_BIN_DIR=""

# 让 pm2 可被找到。npm 的全局 bin 目录经常不在 PATH 里
#（比如 npm 的 prefix 被设成 ~/.npm-global），这里主动补上
pm2_on_path() {
    hash -r
    if have pm2; then
        PM2_BIN_DIR="$(dirname -- "$(command -v pm2)")"
        return 0
    fi

    local prefix candidate
    prefix="$(npm prefix -g 2>/dev/null || true)"
    for candidate in \
        ${prefix:+"$prefix/bin"} \
        "$HOME/.npm-global/bin" \
        "$HOME/.local/bin" \
        /usr/local/bin \
        /usr/bin
    do
        [ -n "$candidate" ] || continue
        if [ -x "$candidate/pm2" ]; then
            export PATH="$candidate:$PATH"
            hash -r
            if have pm2; then
                PM2_BIN_DIR="$candidate"
                return 0
            fi
        fi
    done
    return 1
}

# pm2 所在目录不在登录 PATH 里时，提醒用户写进 ~/.bashrc
warn_pm2_path() {
    [ -n "$PM2_BIN_DIR" ] || return 0
    case ":$ORIGINAL_PATH:" in
        *":$PM2_BIN_DIR:"*) return 0 ;;
    esac
    warn "pm2 所在目录 $PM2_BIN_DIR 不在默认 PATH 中"
    printf '        请把下面这行加到 ~/.bashrc 并重新登录，否则新终端里找不到 pm2：\n'
    printf '            export PATH="%s:$PATH"\n' "$PM2_BIN_DIR"
}

ensure_pm2() {
    step "检查 PM2"
    if pm2_on_path; then
        ok "PM2 已安装：$(pm2 -v 2>/dev/null || echo '?')"
        warn_pm2_path
        return 0
    fi

    local args=()
    [ "$USE_CN" -eq 1 ] && args+=(--registry="$NPM_MIRROR")
    args+=(--no-fund --no-audit)
    log "安装 PM2 ..."
    npm_global pm2 "${args[@]}" || die "PM2 安装失败。可尝试手动执行： npm install -g pm2"

    pm2_on_path || die "PM2 安装后仍找不到 pm2 命令，请检查 npm 全局 bin 目录是否在 PATH 中"
    ok "PM2 安装完成：$(pm2 -v)"
    warn_pm2_path
}

ensure_venv() {
    step "准备 Python 虚拟环境"
    if [ ! -x "$VENV_PY" ]; then
        log "创建虚拟环境 $VENV_DIR ..."
        if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
            warn "python3 -m venv 失败，尝试补装 venv 支持"
            install_packages python3-venv || true
            python3 -m venv "$VENV_DIR" || die "创建虚拟环境失败，请手动安装 python3-venv 后重试"
        fi
    fi
    [ -x "$VENV_PY" ] || die "虚拟环境不完整：$VENV_PY 不存在"

    local pip_args=(install --disable-pip-version-check -q)
    [ "$USE_CN" -eq 1 ] && pip_args+=(-i "$PYPI_MIRROR")

    log "安装 Python 依赖 ..."
    "$VENV_PY" -m pip "${pip_args[@]}" --upgrade pip >/dev/null 2>&1 || warn "升级 pip 失败，继续"
    "$VENV_PY" -m pip "${pip_args[@]}" -r "$REPO_DIR/requirements.txt" \
        || die "依赖安装失败，请检查网络；国内服务器可加 --cn 走镜像"
    ok "Python 依赖就绪 ($("$VENV_PY" -c 'import sys;print(sys.version.split()[0])'))"
}

# ---------------------------------------------------------------------------
# 配置文件
# ---------------------------------------------------------------------------
ensure_config() {
    step "检查配置文件"
    if [ -f "$CONFIG_FILE" ]; then
        ok "已存在 config.yaml（保持不动）"
        return 0
    fi
    [ -f "$CONFIG_TPL" ] || die "找不到配置模板 $CONFIG_TPL"
    cp "$CONFIG_TPL" "$CONFIG_FILE"
    ok "已从模板生成 config.yaml"
}

# 返回 0 表示 cookie 与 sendkey 都已填写
config_filled() {
    [ -f "$CONFIG_FILE" ] || return 1
    "$VENV_PY" - "$CONFIG_FILE" 2>/dev/null <<'PY'
import sys
try:
    import yaml
except Exception:
    sys.exit(0)          # 没装 pyyaml 就不拦
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
except Exception:
    sys.exit(1)
cookie = str(data.get("cookie") or "").strip()
sendkey = str(((data.get("serverchan") or {}).get("sendkey")) or "").strip()
sys.exit(0 if cookie and sendkey else 1)
PY
}

open_editor() {
    local editor="${EDITOR:-}"
    if [ -z "$editor" ]; then
        for candidate in nano vi vim; do
            if have "$candidate"; then editor="$candidate"; break; fi
        done
    fi
    [ -n "$editor" ] || { warn "没有可用的编辑器，请手动编辑 $CONFIG_FILE"; return 1; }
    log "打开编辑器：$editor $CONFIG_FILE"
    "$editor" "$CONFIG_FILE" || warn "编辑器退出码非 0"
}

prompt_config() {
    if config_filled; then
        ok "cookie 与 serverchan.sendkey 均已填写"
        return 0
    fi

    warn "config.yaml 里的 cookie / serverchan.sendkey 还是空的，程序跑起来也拿不到举报数据"

    if [ "$ASSUME_YES" -eq 1 ] || [ ! -t 0 ]; then
        warn "非交互模式，跳过编辑。请稍后手动编辑： $CONFIG_FILE"
        return 1
    fi

    if confirm "现在打开编辑器填写吗？" y; then
        open_editor || true
    fi

    if config_filled; then
        ok "配置已填写完整"
        return 0
    fi

    warn "配置仍不完整。"
    if confirm "仍要继续部署吗？（之后可随时编辑 config.yaml 并 restart）" n; then
        return 1
    fi
    die "已取消部署。请填写 $CONFIG_FILE 后重新运行 bash deploy.sh"
}

# ---------------------------------------------------------------------------
# PM2 进程控制
# ---------------------------------------------------------------------------
pm2_has_app() { pm2 describe "$APP_NAME" >/dev/null 2>&1; }

start_app() {
    step "启动 / 重启服务"
    cd "$REPO_DIR" || die "无法进入项目目录 $REPO_DIR"

    # 让 Python 的 print 立刻进入 pm2 日志，否则会被缓冲住看不见
    export PYTHONUNBUFFERED=1

    hash -r
    if pm2_has_app; then
        log "已存在 PM2 进程「$APP_NAME」，执行重启"
        PYTHONUNBUFFERED=1 pm2 restart "$APP_NAME" --update-env || die "pm2 restart 失败"
    else
        # 注意：绝对不要加 --watch，cache.json 每轮都会被重写，会导致无限重启
        pm2 start "$REPO_DIR/main.py" \
            --name "$APP_NAME" \
            --interpreter "$VENV_PY" \
            --cwd "$REPO_DIR" \
            --restart-delay 3000 \
            --time || die "pm2 start 失败"
    fi
    pm2 save >/dev/null 2>&1 || warn "pm2 save 失败，重启后进程可能不会自动恢复"
    ok "进程已运行"
}

setup_startup() {
    [ "$DO_STARTUP" -eq 1 ] || { warn "已跳过开机自启（--no-startup）"; return 0; }
    step "设置开机自启"

    local run_user run_home out cmd
    run_user="$(id -un)"
    run_home="${HOME:-/root}"

    out="$(env PATH="$PATH" pm2 startup systemd -u "$run_user" --hp "$run_home" 2>&1 || true)"
    cmd="$(printf '%s\n' "$out" | grep -E '^sudo ' | head -n1 || true)"

    if [ -z "$cmd" ]; then
        if systemctl list-unit-files 2>/dev/null | grep -q "^pm2-${run_user}\.service"; then
            ok "开机自启已就绪（systemd: pm2-${run_user}.service）"
        else
            warn "未能自动设置开机自启。可稍后手动执行： pm2 startup"
        fi
        return 0
    fi

    if [ "$(id -u)" -eq 0 ]; then
        eval "${cmd#sudo }" >/dev/null 2>&1 || warn "执行开机自启命令失败，请手动执行： $cmd"
    elif [ -n "$SUDO" ]; then
        $SUDO bash -c "${cmd#sudo }" >/dev/null 2>&1 || warn "执行开机自启命令失败，请手动执行： $cmd"
    else
        warn "请手动执行下面这行以设置开机自启（需要 root）："
        printf '    %s\n' "$cmd"
        return 0
    fi

    if systemctl list-unit-files 2>/dev/null | grep -q "^pm2-${run_user}\.service"; then
        ok "开机自启已设置"
    else
        warn "开机自启状态未知，请用 systemctl status pm2-${run_user} 检查"
    fi
}

print_summary() {
    local status_line
    # 注意：这里的 python -c 片段用 % 格式化，不用 f-string
    #（f-string 里出现反斜杠转义的引号在 Python < 3.12 会直接语法错误）
    # 只显示本脚本管理的那个进程，避免把服务器上其它 pm2 应用也列出来
    status_line="$(pm2 jlist 2>/dev/null | APP_NAME="$APP_NAME" "$VENV_PY" -c '
import json, os, sys
want = os.environ.get("APP_NAME") or ""
try:
    procs = json.load(sys.stdin)
except Exception:
    procs = []
for p in procs:
    name = p.get("name", "?")
    if want and name != want:
        continue
    env = p.get("pm2_env", {})
    print("%-22s %-10s restarts=%s" % (name, env.get("status", "?"), env.get("restart_time", 0)))
' 2>/dev/null || true)"

    printf '\n%s================================================================%s\n' "$C_GREEN" "$C_RESET"
    printf '%s  部署完成%s\n' "$C_GREEN" "$C_RESET"
    printf '%s================================================================%s\n' "$C_GREEN" "$C_RESET"
    printf '  项目目录 : %s\n' "$REPO_DIR"
    printf '  进程名称 : %s\n' "$APP_NAME"
    printf '  解释器   : %s\n' "$VENV_PY"
    printf '  配置文件 : %s\n' "$CONFIG_FILE"
    [ -n "$status_line" ] && printf '  运行状态 : %s\n' "$status_line"
    cat <<EOF

  常用命令：
    pm2 status                       # 查看状态
    pm2 logs $APP_NAME               # 实时日志
    pm2 restart $APP_NAME            # 重启
    bash deploy.sh update            # 拉取更新 + 重启

  改动 config.yaml 后需要重启才生效： pm2 restart $APP_NAME
EOF
    if ! config_filled; then
        printf '\n%s  !! 别忘了填 config.yaml 里的 cookie 和 serverchan.sendkey !!%s\n' "$C_YELLOW" "$C_RESET"
        printf '   编辑后执行： pm2 restart %s\n' "$APP_NAME"
    fi
    printf '\n'
}

# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
cmd_install() {
    printf '\n%s  NGA 举报监听 —— Linux 一键部署 %s\n' "$C_BLUE" "$C_RESET"
    printf '%s  系统: %s | 用户: %s | 目录: %s%s\n\n' "$C_DIM" "$(os_pretty)" "$(id -un)" "$REPO_DIR" "$C_RESET"

    detect_pkg_mgr
    detect_sudo
    [ "$(id -u)" -eq 0 ] && warn "正在以 root 身份部署，PM2 进程也会以 root 运行"

    step "1/6  安装系统基础依赖"
    ensure_base_packages

    ensure_node           # 2/6
    ensure_pm2            # 3/6
    ensure_venv           # 4/6

    step "5/6  配置文件"
    ensure_config
    prompt_config || true

    start_app             # 6/6
    setup_startup
    print_summary
}

cmd_status() {
    pm2_on_path || die "未安装 PM2"
    if pm2_has_app; then
        pm2 describe "$APP_NAME"
    else
        warn "未找到 PM2 进程「$APP_NAME」"
    fi
    printf '\n'
    pm2 list
}

cmd_logs() {
    pm2_on_path || die "未安装 PM2"
    pm2_has_app || die "未找到 PM2 进程「$APP_NAME」，请先执行 bash deploy.sh"
    pm2 logs "$APP_NAME" --lines "$LOG_LINES" --nostream
}

cmd_restart() {
    pm2_on_path || die "未安装 PM2"
    pm2_has_app || die "未找到 PM2 进程「$APP_NAME」，请先执行 bash deploy.sh"
    cd "$REPO_DIR"
    PYTHONUNBUFFERED=1 pm2 restart "$APP_NAME" --update-env
    pm2 save >/dev/null 2>&1 || true
    ok "已重启"
}

cmd_stop() {
    pm2_on_path || die "未安装 PM2"
    pm2_has_app || die "未找到 PM2 进程「$APP_NAME」"
    pm2 stop "$APP_NAME"
    pm2 save >/dev/null 2>&1 || true
    ok "已停止（开机自启仍会拉起，若要彻底关闭请执行 uninstall）"
}

cmd_start() {
    pm2_on_path || die "未安装 PM2"
    pm2_has_app || die "未找到 PM2 进程「$APP_NAME」，请先执行 bash deploy.sh"
    cd "$REPO_DIR"
    PYTHONUNBUFFERED=1 pm2 start "$APP_NAME" --update-env
    pm2 save >/dev/null 2>&1 || true
    ok "已启动"
}

cmd_update() {
    step "更新代码"
    [ -d "$REPO_DIR/.git" ] || die "$REPO_DIR 不是 git 仓库，无法自动更新。请手动上传新代码后执行 bash deploy.sh"

    cd "$REPO_DIR"
    local before after
    before="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
    git pull --ff-only || die "git pull 失败，请检查本地是否有未提交改动"
    after="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"

    if [ "$before" = "$after" ]; then
        ok "代码已是最新（$after）"
    else
        ok "代码已更新：$before -> $after"
    fi

    detect_pkg_mgr
    detect_sudo
    ensure_venv
    cmd_restart
}

cmd_uninstall() {
    step "卸载"
    if pm2_on_path && pm2_has_app; then
        confirm "确认停止并移除 PM2 进程「$APP_NAME」？" n || die "已取消"
        pm2 delete "$APP_NAME" || true
        pm2 save >/dev/null 2>&1 || true
        ok "已移除 PM2 进程"
    else
        warn "没有找到 PM2 进程「$APP_NAME」"
    fi

    if confirm "同时删除 Python 虚拟环境 venv/ ？" n; then
        rm -rf "$VENV_DIR"
        ok "已删除 $VENV_DIR"
    fi

    cat <<EOF

  代码、config.yaml、cache.json 均未删除。
  如需彻底清理：
    pm2 unstartup            # 取消开机自启
    npm uninstall -g pm2     # 卸载 PM2（如果不再需要）
EOF
}

usage() {
    cat <<'EOF'
  NGA 举报监听 —— Linux 一键部署脚本

  用法：
    bash deploy.sh [命令] [选项]

  命令：
    install (默认)   一键部署：装依赖 / Node / PM2，建 venv，装 Python 包，启动并设开机自启
    status           查看 PM2 进程状态
    logs [行数]      查看日志（默认 50 行）
    restart          重启进程
    stop / start     停止 / 启动进程
    update           git pull + 更新依赖 + 重启
    uninstall        停止并移除 PM2 进程（保留代码与配置）
    help             显示本帮助

  选项：
    --cn             国内网络加速（pip 走清华源，npm 走 npmmirror）
    -y, --yes        全程不询问，一律使用默认值
    --no-startup     不设置开机自启
    --name <名字>    自定义 PM2 进程名（默认 nga-listen-report）
    --lines <行数>   配合 logs 使用

  示例：
    sudo bash deploy.sh --cn            # 国内服务器推荐
    sudo bash deploy.sh --cn -y         # 全自动，不询问
    bash deploy.sh logs 200             # 看最近 200 行日志
    bash deploy.sh update               # 更新到最新代码

  环境变量：
    NGA_APP_NAME   等同于 --name
    NGA_REPO_URL   覆盖默认仓库地址
    NGA_DIR        脚本不在仓库里时，克隆到哪个目录（默认 ./NGA_ListenReport）

EOF
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --cn|--china)     USE_CN=1 ;;
            -y|--yes)         ASSUME_YES=1 ;;
            --no-startup)     DO_STARTUP=0 ;;
            --name)           shift; [ "$#" -gt 0 ] || die "--name 需要一个参数"; APP_NAME="$1" ;;
            --name=*)         APP_NAME="${1#*=}" ;;
            --lines)          shift; [ "$#" -gt 0 ] || die "--lines 需要一个参数"; LOG_LINES="$1" ;;
            -h|--help|help)   CMD="help" ;;
            install|deploy)   CMD="install" ;;
            status|log|logs|restart|stop|start|update|uninstall)
                              CMD="$1"; [ "$CMD" = "log" ] && CMD="logs" ;;
            [0-9]*)           LOG_LINES="$1" ;;   # logs 200 这种写法
            -*)               die "未知选项：$1（用 --help 查看用法）" ;;
            *)                die "未知命令：$1（用 --help 查看用法）" ;;
        esac
        shift
    done
}

# 脚本被放在仓库外（curl 直接跑）时，先把仓库克隆下来再重新执行自己
bootstrap_if_needed() {
    [ -f "$REPO_DIR/main.py" ] && return 0

    step "本地没有项目文件，准备克隆仓库"
    log "仓库地址：$REPO_URL"
    log "克隆到  ：$CLONE_DIR"

    detect_pkg_mgr
    detect_sudo
    ensure_base_packages
    have git || die "需要 git 才能克隆仓库，请先安装 git"

    if [ -e "$CLONE_DIR" ] && [ ! -d "$CLONE_DIR/.git" ]; then
        die "$CLONE_DIR 已存在且不是 git 仓库，请先移走它或设置 NGA_DIR 指定其它目录"
    fi
    if [ -d "$CLONE_DIR/.git" ]; then
        log "目标目录已存在仓库，执行 git pull"
        git -C "$CLONE_DIR" pull --ff-only || warn "git pull 失败，继续使用现有代码"
    else
        git clone --depth 1 "$REPO_URL" "$CLONE_DIR" || die "克隆失败，请检查网络；也可手动 git clone 后进入目录执行 bash deploy.sh"
    fi

    [ -f "$CLONE_DIR/deploy.sh" ] || die "克隆下来的仓库里没有 deploy.sh，无法继续。请确认本脚本与仓库版本匹配，或手动把 deploy.sh 放到 $CLONE_DIR 后重试"

    ok "代码已就绪，继续部署"
    exec bash "$CLONE_DIR/deploy.sh" "${ORIG_ARGS[@]}"
}

main() {
    parse_args "$@"
    [ "$CMD" = "help" ] && { usage; exit 0; }

    if [ "$CMD" = "install" ]; then
        bootstrap_if_needed
    fi

    case "$CMD" in
        install)   cmd_install ;;
        status)    cmd_status ;;
        logs)      cmd_logs ;;
        restart)   cmd_restart ;;
        stop)      cmd_stop ;;
        start)     cmd_start ;;
        update)    cmd_update ;;
        uninstall) cmd_uninstall ;;
        *)         usage; exit 1 ;;
    esac
}

main "$@"
