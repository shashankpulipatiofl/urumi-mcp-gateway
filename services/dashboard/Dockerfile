# ---- build stage: compile the React app to static files ----
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# ---- runtime stage: tiny non-root nginx serving the static files ----
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --chmod=0644 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chmod=0755 /app/dist /usr/share/nginx/html
EXPOSE 8080
