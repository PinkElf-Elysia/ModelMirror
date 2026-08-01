FROM nginx:1.27-alpine

COPY office_host/nginx.conf /etc/nginx/conf.d/default.conf
COPY office_addin /usr/share/nginx/html

EXPOSE 8443
