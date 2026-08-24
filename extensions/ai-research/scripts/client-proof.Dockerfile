FROM node@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS build

WORKDIR /app

COPY client/package.json client/package-lock.json ./
RUN npm ci

COPY client/ ./
RUN npm run build

FROM node@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS proof

WORKDIR /proof
COPY --from=build /app/dist ./dist

CMD ["true"]
