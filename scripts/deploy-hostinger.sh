#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_TMP="${ROOT_DIR}/.deploy-hostinger"
HOSTINGER_ENV_FILE="${HOSTINGER_ENV_FILE:-${ROOT_DIR}/.env.hostinger}"

if [[ -f "${HOSTINGER_ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${HOSTINGER_ENV_FILE}"
  set +a
fi

HOSTINGER_HOST="${HOSTINGER_HOST:-}"
HOSTINGER_USER="${HOSTINGER_USER:-}"
HOSTINGER_PORT="${HOSTINGER_PORT:-65002}"
HOSTINGER_REMOTE_DIR="${HOSTINGER_REMOTE_DIR:-}"
HOSTINGER_SSH_KEY="${HOSTINGER_SSH_KEY:-}"
HOSTINGER_PASS="${HOSTINGER_PASS:-}"
HOSTINGER_INSTALL_PLAYWRIGHT="${HOSTINGER_INSTALL_PLAYWRIGHT:-1}"
HOSTINGER_RUN_NPM_INSTALL="${HOSTINGER_RUN_NPM_INSTALL:-1}"
HOSTINGER_NPM_BIN="${HOSTINGER_NPM_BIN:-npm}"
HOSTINGER_NPX_BIN="${HOSTINGER_NPX_BIN:-npx}"

if [[ -z "${HOSTINGER_HOST}" || -z "${HOSTINGER_USER}" || -z "${HOSTINGER_REMOTE_DIR}" ]]; then
  cat <<'EOF'
Faltam variáveis obrigatórias para o deploy.

Defina:
  HOSTINGER_HOST
  HOSTINGER_USER
  HOSTINGER_REMOTE_DIR

Opcionais:
  HOSTINGER_PORT=65002
  HOSTINGER_SSH_KEY=/caminho/para/chave
  HOSTINGER_PASS=sua-senha-ssh
  HOSTINGER_RUN_NPM_INSTALL=1
  HOSTINGER_INSTALL_PLAYWRIGHT=1
  HOSTINGER_NPM_BIN=/caminho/para/npm
  HOSTINGER_NPX_BIN=/caminho/para/npx

Exemplo:
  HOSTINGER_HOST=31.97.x.x \
  HOSTINGER_USER=u123456789 \
  HOSTINGER_REMOTE_DIR=/home/u123456789/apps/skyscanner \
  HOSTINGER_PORT=65002 \
  ./scripts/deploy-hostinger.sh
EOF
  exit 1
fi

SSH_OPTS=(-p "${HOSTINGER_PORT}" -o StrictHostKeyChecking=accept-new)
if [[ -n "${HOSTINGER_SSH_KEY}" ]]; then
  SSH_OPTS+=(-i "${HOSTINGER_SSH_KEY}")
fi

SSH_CMD=(ssh "${SSH_OPTS[@]}")
RSYNC_RSH="ssh ${SSH_OPTS[*]}"
if [[ -n "${HOSTINGER_PASS}" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "sshpass não está instalado. Instale para usar HOSTINGER_PASS."
    exit 1
  fi
  SSH_CMD=(sshpass -p "${HOSTINGER_PASS}" ssh "${SSH_OPTS[@]}")
  RSYNC_RSH="sshpass -p '${HOSTINGER_PASS}' ssh ${SSH_OPTS[*]}"
fi

REMOTE="${HOSTINGER_USER}@${HOSTINGER_HOST}"

echo "[1/5] Typecheck"
cd "${ROOT_DIR}"
npm run typecheck

echo "[2/5] Build"
npm run build

echo "[3/5] Preparando pacote"
rm -rf "${DEPLOY_TMP}"
mkdir -p "${DEPLOY_TMP}"
cp -R dist "${DEPLOY_TMP}/dist"
cp -R static "${DEPLOY_TMP}/static"
cp package.json package-lock.json "${DEPLOY_TMP}/"

echo "[4/5] Enviando arquivos"
"${SSH_CMD[@]}" "${REMOTE}" "mkdir -p '${HOSTINGER_REMOTE_DIR}'"
rsync -az --delete \
  -e "${RSYNC_RSH}" \
  "${DEPLOY_TMP}/" "${REMOTE}:${HOSTINGER_REMOTE_DIR}/"

echo "[5/5] Instalando dependências no servidor"
REMOTE_CMD="cd '${HOSTINGER_REMOTE_DIR}'"
if [[ "${HOSTINGER_RUN_NPM_INSTALL}" == "1" ]]; then
  REMOTE_CMD+=" && ${HOSTINGER_NPM_BIN} install --omit=dev"
fi
if [[ "${HOSTINGER_INSTALL_PLAYWRIGHT}" == "1" ]]; then
  REMOTE_CMD+=" && ${HOSTINGER_NPX_BIN} playwright install chromium"
fi

"${SSH_CMD[@]}" "${REMOTE}" "${REMOTE_CMD}"

rm -rf "${DEPLOY_TMP}"
echo "Deploy concluído para ${REMOTE}:${HOSTINGER_REMOTE_DIR}"
