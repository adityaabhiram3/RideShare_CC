FROM mongo:latest

COPY run_worker.sh /docker-entrypoint-initdb.d/

RUN apt update && apt install -y python3 python3-pip

RUN pip3 install flask gevent kazoo pika pymongo
