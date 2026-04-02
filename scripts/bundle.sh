#!/bin/bash
set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

echo "==> 构建 Docker 镜像..."
docker build -t infill:latest .

echo "==> 导出镜像..."
docker save infill:latest | gzip > /tmp/infill-image.tar.gz

echo "==> 打包部署包..."
BUNDLE_DIR=$(mktemp -d)
mkdir -p "$BUNDLE_DIR/data"

# 镜像
mv /tmp/infill-image.tar.gz "$BUNDLE_DIR/"

# catalog 示例（首次部署时 deploy.sh 会复制为 catalog.yaml）
cp "$ROOT/data/catalog.yaml.example" "$BUNDLE_DIR/data/"

# docker-compose.yml
cat > "$BUNDLE_DIR/docker-compose.yml" << 'YAML'
services:
  app:
    image: infill:latest
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    restart: unless-stopped
YAML

# 部署脚本
cat > "$BUNDLE_DIR/deploy.sh" << 'SCRIPT'
#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "==> 加载镜像..."
docker load < infill-image.tar.gz

if [ ! -f data/catalog.yaml ]; then
  echo "==> 未找到 data/catalog.yaml，使用示例文件"
  cp data/catalog.yaml.example data/catalog.yaml
fi

echo "==> 启动服务..."
docker compose up -d

echo ""
echo "============================="
echo " 部署完成！访问 http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):8000"
echo " "
echo " 修改产品目录：编辑 data/catalog.yaml 后在网页上点'重新加载目录'"
echo " 查看日志：docker compose logs -f"
echo " 停止服务：docker compose down"
echo " 更新部署：重新运行 ./deploy.sh"
echo "============================="
SCRIPT
chmod +x "$BUNDLE_DIR/deploy.sh"

# 打成最终的 tar.gz
OUTPUT="$ROOT/infill-deploy.tar.gz"
tar -czf "$OUTPUT" -C "$BUNDLE_DIR" .

rm -rf "$BUNDLE_DIR"

SIZE=$(du -h "$OUTPUT" | cut -f1)
echo ""
echo "============================="
echo " 打包完成！"
echo " 文件：$OUTPUT ($SIZE)"
echo ""
echo " 部署步骤："
echo "   1. 拷贝到服务器：scp infill-deploy.tar.gz user@server:~/"
echo "   2. 在服务器上："
echo "      mkdir infill && cd infill"
echo "      tar xzf ~/infill-deploy.tar.gz"
echo "      ./deploy.sh"
echo "============================="
