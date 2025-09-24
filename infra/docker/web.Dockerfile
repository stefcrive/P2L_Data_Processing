# syntax=docker/dockerfile:1

FROM node:20-alpine AS dev
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json* ./apps/web/
RUN cd apps/web && npm install || true
EXPOSE 3000

