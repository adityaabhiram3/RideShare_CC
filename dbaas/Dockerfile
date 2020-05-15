FROM ubuntu:latest

WORKDIR /src

RUN apt update && apt install -y python3 python3-pip

RUN pip3 install docker flask gevent kazoo pika pymongo

CMD ["python3", "orchestrator.py"]
