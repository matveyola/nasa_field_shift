FROM node:24-alpine AS build
# «Польовий помічник» is built from the agent's widget source (named context agent-widget, see compose.yaml),
# at the same relative place as in the repository, so vite.config.ts finds it.
WORKDIR /src/apps/web
COPY package.json package-lock.json ./
RUN npm ci
COPY --from=agent-widget . /src/services/agent/frontend/src/widget
COPY --from=planning-contracts . /src/contracts/planning-v1
COPY . .
ARG VITE_MAPTILER_KEY
RUN npm run build

FROM nginx:1.28-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/apps/web/dist /usr/share/nginx/html
EXPOSE 80
