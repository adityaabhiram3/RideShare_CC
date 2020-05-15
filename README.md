# Final Project - RideShare
For the final project, three Ubuntu EC2 instances are used - Rides, Users and Orchestrator. The rides_microservices folder is present in Rides VM, the users_microservices folder is present in Users VM and dbaas folder is present in Orchestrator. In all three instances, the latest versions of docker and docker-compose are installed. An AWS Loadbalancer is configured as necessary to route requests to the Rides and Users VMs.

Commands to be run:
- Rides VM:
    - `sudo docker-compose up` in the rides_microservices folder
- Users VM:
    - `sudo docker-compose up` in the users_microservices folder
- Orchestrator:
    - `sudo docker build -t worker:latest workers` in the dbaas folder
    - `sudo docker-compose up` in the dbaas folder

[Project Report](../master/Cloud%20Computing%20Report%20-%20CC_0107_0175_1133_1501.pdf)
