FROM node:22-alpine

ENV NODE_ENV=production PORT=8080
WORKDIR /app
COPY package.json server.js ./

USER node
EXPOSE 8080
# wget, not curl: see repo CLAUDE.md (Alpine curl + Docker DNS)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
CMD ["node", "server.js"]
