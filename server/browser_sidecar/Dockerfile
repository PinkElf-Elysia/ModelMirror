FROM mcr.microsoft.com/playwright:v1.58.2-noble

ENV NODE_ENV=production
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV BROWSER_SOCKET_PATH=/run/modelmirror-browser/browser.sock
ENV BROWSER_DATA_ROOT=/browser-data

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev --ignore-scripts \
    && npm cache clean --force

COPY policy.mjs server.mjs ./
COPY THIRD_PARTY_NOTICES.md /usr/share/doc/modelmirror-browser/THIRD_PARTY_NOTICES.md

RUN mkdir -p /run/modelmirror-browser /browser-data /sandbox-workspaces \
    && chown -R pwuser:pwuser /run/modelmirror-browser /browser-data /app

USER pwuser

CMD ["node", "server.mjs"]
