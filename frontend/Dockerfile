FROM node:22-alpine AS build
WORKDIR /app
ARG VITE_CHAT_API_URL=http://localhost:8000/api/chat
ENV VITE_CHAT_API_URL=$VITE_CHAT_API_URL
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist/client /usr/share/nginx/html
EXPOSE 80
