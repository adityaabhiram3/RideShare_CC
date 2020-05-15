#!/usr/bin/python
# -*- coding: utf-8 -*-
import docker
import flask
import json
import logging
import pika
import pymongo
import subprocess
import threading
import uuid
from time import sleep
from kazoo.client import KazooClient

logging.basicConfig(level=logging.WARNING)

sleep(2) #Wait for zookeeper startup

zk = KazooClient(hosts='zookeeper:2181')
zk.start()

zk.ensure_path('/workers') #all workers create an ephemeral node as a child of this path

zk.create('/scale', b'1') #/scale znode contains number of slaves that need to be running at anytime

inited = False #inited will be set to True once
scaling = False #scaling will be set to True only while autoscaling is happening

@zk.ChildrenWatch('/workers', send_event=True) #watch children of /workers znode
def high_availability(children, event):
    #Spawn a new worker, if a worker crashed
    if inited and not scaling:
        data, stat = zk.get('/scale')
        expected = int(data.decode('utf-8')) + 1 #slaves + master
        if len(children) < expected:
            logging.warning('spawning a worker [fault tolerance]')
            spawn_workers(1)
            sleep(3) #wait for worker to initalize and create ephemeral znode
            set_master_pid()

def get_pid(worker):
    #Return the PID of the given worker as str
    return subprocess.check_output('docker inspect --format \'{{ .State.Pid }}\' worker'+ str(worker),
                                    shell=True).decode('utf-8')[:-1]

sleep(10) #wait for rabbitmq to startup

credentials = pika.PlainCredentials('guest', 'guest')
parameters = pika.ConnectionParameters('rabbitmq', 5672, '/', credentials, heartbeat=0)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

channel.exchange_declare(exchange='writeX', exchange_type='direct')
channel.exchange_declare(exchange='readX', exchange_type='direct')
channel.exchange_declare(exchange='syncX', exchange_type='fanout')

def on_response(ch, method, properties, body):
    #Callback for responseq, to store the response received in the global variable
    global response
    if corr_id == properties.correlation_id:
        response = body

channel.queue_declare(queue='responseQ')
channel.basic_consume(queue='responseQ', on_message_callback=on_response, auto_ack=True)

commonDB = pymongo.MongoClient('mongodb://common:27017')['db'] #DB used for replication

#docker.sock of the instance is mounted on the container at this path
client = docker.DockerClient(base_url='unix://var/run/docker.sock')

workers = dict() #dict with worker number as key and the Container object as value
pids = dict() #dict with pid as key and worker num as value

def get_master_pid():
    #Return the master's pid as str, by reading the /master znode
    data, stat = zk.get('/master')
    return data.decode('utf-8')

def set_master_pid():
    #Set the master by choosing the worker with the lowest pid
    workers = zk.get_children('/workers')
    global pids
    pids = dict()
    #Refresh the pids dict to contain only currently running workers
    for worker in workers:
        pids[get_pid(worker[6:])] = worker[6:]
    master_pid = str(min([int(pid) for pid in pids.keys()]))
    zk.set('/master', bytes(master_pid, 'utf-8'))

worker_num = 1 #used to assign number to workers
round_robin = 0 #used to send readq messages in round-robin fashion
corr_id = None #usedto store correlation ID of readq message
response = None #used to store response from responseq

def spawn_workers(n):
    #Spawn n workers
    global worker_num
    for i in range(n):
        worker_name = 'worker' + str(worker_num)
        workers[str(worker_num)] = client.containers.run('worker:latest', name=worker_name, environment=['TEAM_NAME=CC_0107_0175_1133_1501', 'WORKER_NAME='+worker_name],
                                                          network='dbaas', volumes={'/home/ubuntu/dbaas/workers/worker.py': {'bind': '/src/worker.py', 'mode': 'ro'},
                                                                                    '/usr/bin/docker': {'bind': '/usr/bin/docker', 'mode': 'ro'},
                                                                                    '/var/run/docker.sock': {'bind': '/var/run/docker.sock', 'mode': 'ro'}},
                                                          command='--quiet', detach=True)
        pids[get_pid(str(worker_num))] = str(worker_num)
        worker_num += 1

spawn_workers(2) #initial configuration - 1 master & 1 slave workers

master_pid = str(min([int(pid) for pid in pids.keys()]))
zk.create('/master', bytes(master_pid, 'utf-8'))

sleep(6) #wait for workers to initialize

inited = True

app = flask.Flask(__name__)

read_count = 0 #used to count number of read requests in a two minute period
scaler_init = False #scaler_init is set to True once the first read request is received

def scale():
    #Scsle out or in as needed as per number of read requests received in last two minutes
    threading.Timer(120, scale).start() #runs the scaling function in a new thread in 120s seconds
    global scaling
    global read_count
    if scaler_init:
        slaves_needed = read_count//20
        if read_count==0:
            slaves_needed = 1
        elif read_count%20:
            slaves_needed += 1
        read_count = 0 #reset read requests count
        slaves_running = len(pids) - 1 #total workers minus master
        logging.warning(f'Slaves: Running={slaves_running} | Needed={slaves_needed}')
        if slaves_running != slaves_needed:
            scaling = True
            if slaves_needed > slaves_running:
                #Scale out
                logging.warning(f'spawning {slaves_needed - slaves_running} slaves')
                spawn_workers(slaves_needed - slaves_running)
                sleep(3)
                set_master_pid()
            elif slaves_needed < slaves_running:
                #Scale in
                logging.warning(f'killing {slaves_running - slaves_needed} slaves')
                for _ in range(slaves_running - slaves_needed):
                    slave_pid = max([int(pid) for pid in pids.keys()])
                    workers[pids[str(slave_pid)]].kill()
                    pids.pop(str(slave_pid))
            zk.set('/scale', bytes(str(slaves_needed), 'utf-8')) #set the new scale
            scaling = False

def increaseReadCount(response):
    #Increase read count if request is to read API
    global read_count
    global scaler_init
    if '/api/v1/db/read' in flask._request_ctx_stack.top.request.url:
        if not scaler_init:
            scale()
            scaler_init = True #after first read request, scaling is enabled
        read_count += 1
    return response

app.after_request(increaseReadCount)

@app.route('/api/v1/db/write', methods=['POST'])
def writeToDB():
    #Send write message to master through the writeX direct exchange with master's pid as routing key
    body = json.dumps(flask.request.get_json())
    channel.basic_publish(exchange='writeX', routing_key=get_master_pid(), body=body)
    commonDB['writes'].insert_one({'body':body})
    return flask.Response('{}', status=200, mimetype='application/json')

@app.route('/api/v1/db/read', methods=['POST'])
def readFromDB():
    #Send read message to a slave through the readX direct exchange with slave's pid as routing key
    #The slave is chosen in round-robin fashion
    body = json.dumps(flask.request.get_json())
    global round_robin
    global response
    global corr_id
    slave_list = [int(pid) for pid in pids.keys()]
    slave_list.sort()
    slave_list = slave_list[1:] #remove the master from slave list
    round_robin = (round_robin + 1) % len(slave_list) #get next slave to be chosen in round-robin
    corr_id = str(uuid.uuid4())
    response = None
    channel.basic_publish(exchange='readX', routing_key=str(slave_list[round_robin]), body=body, 
                          properties=pika.BasicProperties(reply_to='responseQ',correlation_id=corr_id))
    while response is None:
        #Wait until corresponding response is received from the slave
        connection.process_data_events()
    read_data = response
    response = None
    return flask.Response(read_data, status=200, mimetype='application/json')

@app.route('/api/v1/db/clear', methods=['POST'])
def clearDB():
    #Send message to master through writeX direct exchange to clear its DB
    channel.basic_publish(exchange='writeX', routing_key=get_master_pid(), body='clear')
    commonDB['writes'].drop() #clear the DB used for replication purpose
    return flask.Response('{}', status=200, mimetype='application/json')

@app.route('/api/v1/worker/list', methods=['GET'])
def listWorkers():
    #Return a JSON array of the pids of all workers, sorted in ascending order
    workers = [int(pid) for pid in pids.keys()]
    workers.sort()
    return flask.Response(json.dumps(workers), status=200, mimetype='application/json')

@app.route('/api/v1/crash/master', methods=['POST'])
def crashMaster():
    #Crash the master worker and return its pid as a JSON array
    master_pid = get_master_pid()
    master = 'worker' + pids[master_pid]
    subprocess.run(['docker', 'kill', master])
    return flask.Response(json.dumps([int(master_pid)]), status=200, mimetype='application/json')

@app.route('/api/v1/crash/slave', methods=['POST'])
def crashSlave():
    #Crash the slave worker with highest pid and return its pid as a JSON array
    slave_pid = max([int(pid) for pid in pids.keys()])
    slave = 'worker' + pids[str(slave_pid)]
    subprocess.run(['docker', 'kill', slave])
    return flask.Response(json.dumps([slave_pid]), status=200, mimetype='application/json')

if __name__=='__main__':
    app.run(debug=True, host='0.0.0.0', use_reloader=False)
