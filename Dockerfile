FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
COPY nginx.conf /etc/nginx/nginx.conf
ENV PORT=8080
EXPOSE 8080
CMD sh -c "envsubst '\$PORT' < /etc/nginx/nginx.conf > /etc/nginx/nginx.conf.tmp && mv /etc/nginx/nginx.conf.tmp /etc/nginx/nginx.conf && nginx -g 'daemon off;'"
