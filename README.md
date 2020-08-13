# README #

This is the DNS update lambda in "serverless" (https://serverless.com/) form.

The DNS update lambda runs periodically and updates the nominated Route 53 zone from the nominated DNS server.

### What is this repository for? ###

* Synchronize a Route 53 Hosted Zone with a local DNS
* Version 1.0


### How do I get set up? ###

* Clone this repo
* Set up Python (you probably will want to set up a virtual environment.  See https://virtualenv.pypa.io/en/stable/)
* Dependencies: 
    * boto3 from AWS
	* dnspython (http://www.dnspython.org/examples.html)
	* npm init && npm install --save serverless-python-requirements  ( see https://serverless.com/blog/serverless-python-packaging/)
	* You will need Docker installed (see the link in the previous point)

* Deploy with sls deploy -v


### Who do I talk to? ###

* Mike Nielsen 
    * rewrote the Lambda mentioned here: https://aws.amazon.com/blogs/compute/powering-secondary-dns-in-a-vpc-using-aws-lambda-and-amazon-route-53-private-hosted-zones/
    * wrote the serverless.yml file
