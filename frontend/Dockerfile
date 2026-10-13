FROM node:20-alpine AS build

WORKDIR /app

# 必须同时 COPY package-lock.json：`npm ci` 依赖 lock 文件做确定性安装，
# 只给 package.json 会报：
#   The `npm ci` command can only install with an existing package-lock.json
#
# 关键：package.json 里**不能**手写 `@esbuild/win32-x64` 这类平台专属依赖。
# 之前写死了它，导致 lock 只能装在 Windows 上，容器构建直接失败：
#   notsup Unsupported platform for @esbuild/win32-x64 (current: linux)
# 移除后 esbuild/rollup 会在安装时按平台自动选二进制，lock 恢复跨平台。
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
