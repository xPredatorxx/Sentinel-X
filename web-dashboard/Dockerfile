# ---- étape 1 : compilation du dashboard ----
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
# npm ci : installation reproductible depuis le lockfile, sans scripts post-install
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . .
RUN npm run build

# ---- étape 2 : Nginx non-root (utilisateur 101, ports 8080/8443) ----
FROM nginxinc/nginx-unprivileged:stable-alpine
# seuls les fichiers compilés sont copiés : pas de code source, pas de node_modules
COPY --from=build /src/dist /usr/share/nginx/html
USER 101
EXPOSE 8080 8443
