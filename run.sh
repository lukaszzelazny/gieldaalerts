docker build -t gieldaalerts:latest .
docker run --env-file .env -p 8000:8000 gieldaalerts:latest