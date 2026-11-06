FROM node:24.18.0-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src
COPY content ./content
COPY runtime ./runtime
COPY context ./context
COPY fixtures ./fixtures
COPY tooling ./tooling
COPY ui-host ./ui-host
COPY docs/RPG04_PROTOCOL_RUNTIME.txt ./docs/RPG04_PROTOCOL_RUNTIME.txt
COPY plugins ./plugins
COPY ancient-card ./ancient-card
COPY blue-fish-card ./blue-fish-card
COPY god-card/public ./god-card/public
COPY god-card/lib ./god-card/lib
COPY god-card/resources ./god-card/resources
COPY cultivation-card ./cultivation-card
COPY xingjian-card ./xingjian-card
COPY lord-of-mysteries-card/public ./lord-of-mysteries-card/public
COPY lord-of-mysteries-card/resources ./lord-of-mysteries-card/resources
COPY lord-of-mysteries-card/assembly.mjs ./lord-of-mysteries-card/assembly.mjs
COPY studio ./studio
COPY ui/package.json ui/package-lock.json ./ui/
RUN npm ci --prefix ui
COPY ui/src ./ui/src
COPY ui/index.html ui/tsconfig.json ui/vite.config.ts ./ui/
RUN npm run build --prefix ui -- --base=/rpg-app/rpg05/
COPY card-replica/package.json card-replica/package-lock.json ./card-replica/
RUN npm ci --prefix card-replica
COPY card-replica/src ./card-replica/src
COPY card-replica/lib ./card-replica/lib
COPY card-replica/resources ./card-replica/resources
COPY card-replica/public ./card-replica/public
COPY card-replica/index.html card-replica/god.html card-replica/vite.config.mjs card-replica/tsconfig.json ./card-replica/
RUN npm run build --prefix card-replica -- --base=/rpg-app/earth/
FROM node:24.18.0-alpine
WORKDIR /app
COPY --from=build /app /app
ENV RPG_BIND=0.0.0.0 PORT=18420 RPG_DATA_DIR=/data
RUN mkdir /data && chown node:node /data
USER node
EXPOSE 18420
CMD ["node","studio/container-host.mjs"]
