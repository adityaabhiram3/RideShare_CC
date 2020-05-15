#!/usr/bin/python
# -*- coding: utf-8 -*-
import json
import logging
import pika
import pymongo
import subprocess
from time import sleep
from kazoo.client import KazooClient

logging.basicConfig(level=logging.WARNING)

#Get name of the worker form environment variable
worker_name = subprocess.check_output('printenv WORKER_NAME', shell=True).decode('utf-8')[:-1]

#Get pid of the worker container as a str
pid = subprocess.check_output('docker inspect --format \'{{ .State.Pid }}\' '+ worker_name,
                               shell=True).decode('utf-8')[:-1]

zk = KazooClient(hosts='zookeeper:2181')
zk.start()
zk.create('/workers/'+worker_name, bytes(pid, 'utf-8'), ephemeral=True)

is_master = None

@zk.DataWatch('/master') #/master znode is watched to update role of worker
def master_check(data, stat, event=None):
    #Set role of worker to slave or master by reading the /master znode
    if data is not None:
        global is_master
        if int(pid) == int(data.decode('utf-8')):
            logging.warning(worker_name + ' is master')
            is_master = True
        else:
            logging.warning(worker_name + ' is slave')
            is_master = False

database = pymongo.MongoClient('mongodb://localhost:27017')['db'] #worker's DB
commonDB = pymongo.MongoClient('mongodb://common:27017')['db'] #common DB used for replication

credentials = pika.PlainCredentials('guest', 'guest')
parameters = pika.ConnectionParameters('rabbitmq', 5672, '/', credentials, heartbeat=0)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

#Replication using common DB
for write in commonDB['writes'].find({}, {'_id': False}):
    body = write['body']
    body = json.loads(body)
    flag, table, column, data = body['flag'], database[body['table']], body['column'], body['data']
    d = dict()
    for i in range(len(data)):
        d[column[i]] = data[i]
    #flag is [w]rite or [d]elete
    if flag == 'w':
        table.insert_one(d)
    elif flag == 'd':
        table.remove(d)
    logging.warning(f'I replicated {body}')

#Two queues are created and bound to writeX and readX exchanges with worker's pid as routing key
channel.queue_declare(queue='writeQ'+worker_name)
channel.queue_bind(exchange='writeX', queue='writeQ'+worker_name, routing_key=pid)
channel.queue_declare(queue='readQ'+worker_name)
channel.queue_bind(exchange='readX', queue='readQ'+worker_name, routing_key=pid)
#A queue is created and bound to syncX fanout exchange
channel.queue_declare(queue='syncQ'+worker_name)
channel.queue_bind(exchange='syncX', queue='syncQ'+worker_name)

def writeToDatabase(ch, method, properties, body):
    #Callback for writeq; Write the change into the DB and sync slave workers
    #Publish to sync queues through syncX fanout exchange
    channel.basic_publish(exchange='syncX', routing_key='', body=body)
    if body==b'clear':
        #If message is b'clear'(sent by /db/clear API), then the database is cleared
        database.command("dropDatabase")
        logging.warning('I cleared db')
        return
    body = json.loads(body)
    flag, table, column, data = body['flag'], database[body['table']], body['column'], body['data']
    d = dict()
    for i in range(len(data)):
        d[column[i]] = data[i]
    #flag is [w]rite or [d]elete
    if flag == 'w':
        table.insert_one(d)
    elif flag == 'd':
        table.remove(d)
    logging.warning(f'I wrote {body}')

def readFromDatabase(ch, method, properties, body):
    #Callback for read queue; Read the DB to find documents matching the body
    body = json.loads(body)
    table, column, data = database[body['table']], body['column'], body['data']
    d = dict()
    if data != ['']:
        for i in range(len(data)):
            d[column[i]] = data[i]
    l = [_ for _ in table.find(d, {'_id': False})]
    if l: #send list as response if atleast one document matches
        ch.basic_publish(exchange='', routing_key=properties.reply_to, properties=pika.BasicProperties(
                        correlation_id=properties.correlation_id), body=json.dumps(l))
    else: #send '{}' as response
        ch.basic_publish(exchange='', routing_key=properties.reply_to, properties=pika.BasicProperties(
                        correlation_id=properties.correlation_id), body='{}')
    logging.warning(f'I read {l}')
    ch.basic_ack(delivery_tag = method.delivery_tag)

def syncDatabase(ch, method, properties, body):
    #Callback of sync queue; Sync database of slave with master
    if not is_master: #Callback does work only if it is in a slave worker
        if body==b'clear':
            database.command("dropDatabase")
            logging.warning('db cleared')
            return
        body = json.loads(body)
        flag, table, column, data = body['flag'], database[body['table']], body['column'], body['data']
        d = dict()
        for i in range(len(data)):
            d[column[i]] = data[i]
        #flag is [w]rite or [d]elete
        if flag == 'w':
            table.insert_one(d)
        elif flag == 'd':
            table.remove(d)
        logging.warning(f'I synced {body}')

channel.basic_consume(queue='writeQ'+worker_name, on_message_callback=writeToDatabase, auto_ack=True)
channel.basic_consume(queue='readQ'+worker_name, on_message_callback=readFromDatabase)
channel.basic_consume(queue='syncQ'+worker_name, on_message_callback=syncDatabase, auto_ack=True)

channel.start_consuming()
