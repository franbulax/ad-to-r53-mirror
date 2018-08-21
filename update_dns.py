import dns
import dns.zone
import dns.query
import dns.rdatatype
import logging
import os
import sys
import json
import boto3

#
# Periodically update a Rout53 zone with changes from a nominated DNS server
# Note:
#  The server must allow zone-transfers
#  This function can get confused if you have domain names with "." in them and have a subdomain that matches the components of a domain name
r53 = boto3.client('route53')
    
def make_logger():
    logLevel = os.environ.get("LOGLEVEL")

    nlevel = getattr(logging,logLevel.upper(),logging.DEBUG)

    if not isinstance(nlevel,int):
        raise ValueError("Invalid log level: {}".format(nlevel))

    logging.basicConfig(level=nlevel, format='(%(asctime)s %(threadName)-10s) %(message)s')
    log = logging.getLogger("update_dns")
    log.setLevel(nlevel)
    return log

log = make_logger()

def make_short_name(domain,name):
    sn = name.replace(domain,'').rstrip('.')
    if sn == '':
        sn = '@'
    return sn

def make_zone_from_r53(domainName,zoneId):
    log.debug("Making zone in {} from zoneId {}".format(domainName,zoneId))
    vpcZone = dns.zone.Zone(origin=domainName)
    vpcResponse = r53.list_resource_record_sets(HostedZoneId=zoneId)
    vpcRecordSet = vpcResponse['ResourceRecordSets']
    while vpcResponse['IsTruncated']:
        vpcResponse = r53.list_resource_record_sets(HostedZoneId=zoneId,StartRecordName=vpcResponse['NextRecordName'],MaxItems='100')
        vpcRecordSet.extend(vpcResponse['ResourceRecordSets'])

    log.debug("Have a total of {:d} record sets".format(len(vpcRecordSet)))
    for record in vpcRecordSet:
        recordName = make_short_name(domainName,record['Name'])
        # find/create the rdataset in the new zone
        rdataset = vpcZone.find_rdataset(recordName,rdtype=record['Type'],create=True)
        for rrec in record['ResourceRecords']:
            rdata = dns.rdata.from_text(1,rdataset.rdtype,make_short_name(domainName,rrec['Value']))
            rdataset.add(rdata,ttl=record['TTL'])
    return vpcZone


def get_master_zone(ip,name):
    log.debug("Getting master zone: {} {}".format(ip,name))
    try:
        zone = dns.zone.from_xfr(dns.query.xfr(ip,name))
        return zone
    except:
        log.error("Failed initial zone transfer: {}".format(sys.exc_info()))
        raise


# Compute the difference between zones
def diff_zones(zoneId,domainName,serverIp):
    differences = []
    masterZone = get_master_zone(serverIp,domainName)
    r53Zone = make_zone_from_r53(domainName,zoneId)

    r53KeySet = set(r53Zone.keys())
    masterKeySet = set(masterZone.keys())
    # Compare serials
    r53Soa = r53Zone.get_rdataset('@','SOA')
    r53Serial = r53Soa[0].serial
    masterSoa = masterZone.get_rdataset('@','SOA')
    masterSerial = masterSoa[0].serial
    if(r53Serial > masterSerial):
        log.error("Route 53 domain {} serial {:d} > dns serial {:d}".format(domainName,r53Serial,masterserial));
        return differences
    
    # Get names in master not in r53
    # so we can create them in r53
    mNotInR = masterKeySet - r53KeySet
    for name in mNotInR:
        node = masterZone[name]
        for rec in node:
            changeRec = []
            for val in rec:
                changeRec.append(val)
            change={'name':name,
                    'type':dns.rdatatype.to_text(rec.rdtype),
                    'changeRec':changeRec,
                    'ttl':rec.ttl,
                    'action':'CREATE'}
            if change not in differences:
                differences.append(change)

    # Get names in r53 not in master
    # so we can delete them from r53
    rNotInM = r53KeySet - masterKeySet
    for name in rNotInM:
        node = r53Zone[name]
        for rec in node:
            changeRec = []
            for val in rec:
                changeRec.append(val)
            change={'name':name,
                    'type':dns.rdatatype.to_text(rec.rdtype),
                    'changeRec':changeRec,
                    'ttl':rec.ttl,
                    'action':'DELETE'}
            if change not in differences:
                differences.append(change)

    # now process names common to both
    common = r53KeySet & masterKeySet
    for name in common:
        r53Node = r53Zone[name]
        masterNode = masterZone[name]
        # Inspect each r53 record in the R53 node
        # Get the corresponding master record from the corresponding master node
        # If the master record and the r53 record differ
        # Then, If the master record exists, then update the r53 record
        # otherwise delete it
        for r53Rec in r53Node:
            mRec = masterNode.get_rdataset(r53Rec.rdclass,r53Rec.rdtype)
            if r53Rec != mRec:
                changeRec = []
                if mRec:
                    action = 'UPSERT'
                    for val in mRec:
                        log.debug("R53 Upsert from mRec of {}".format(val))
                        changeRec.append(val)
                else:
                    action = 'DELETE'
                    for val in r53Rec:
                        log.debug("Delete of {}".format(val))
                        changeRec.append(val)
                change = {'name':name,
                          'type':dns.rdatatype.to_text(r53Rec.rdtype),
                          'changeRec':changeRec,
                          'ttl':mRec.ttl,
                          'action':action}
                if change not in differences:
                    differences.append(change)
        # Inspect each master record in the master node
        # Get the corresonding R53 record from the corresponding R53 node
        # if the r53 record and the master record differ
        # Then, if the r53 record exists, update it
        # otherwise create it
        for mRec in masterNode:
            r53Rec = r53Node.get_rdataset(mRec.rdclass,mRec.rdtype)
            if mRec != r53Rec:
                changeRec = []
                if r53Rec:
                    action = 'UPSERT'
                    for val in mRec:
                        log.debug("Master Upsert of {}".format(val))
                        changeRec.append(val)
                else:
                    action = 'CREATE'
                    for val in mRec:
                        log.debug("Create of {}".format(val))
                        changeRec.append(val)
                change = {'name':name,
                          'type':dns.rdatatype.to_text(mRec.rdtype),
                          'changeRec':changeRec,
                          'ttl':mRec.ttl,
                          'action':action}
                if change not in differences:
                    differences.append(change)
            if mRec.rdtype == dns.rdatatype.SOA or not r53Rec:
                continue

            elif mRec.ttl != r53Rec.ttl:
                changeRec = []
                for val in mRec:
                    changeRec.append(val)
                change = {'name':name,
                          'type':dns.rdatatype.to_text(mRec.rdtype),
                          'changeRec':changeRec,
                          'ttl':mRec.ttl,
                          'action':'UPSERT'}
                if change not in differences:
                    differences.append(change)
    # Filter out changes we're not going to make
    # i.e. to the NS records for this domain
#    filtered = [el for el in differences if not (el['type'] == 'NS' and el['name'].to_text() == '@') ]
    filtered = [el for el in differences if not (el['type'] in ['NS','SOA'] and el['name'].to_text() == '@')]

    return filtered

def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]

def update(event,context):
    log.debug("Starting update")
    #
    # Environment varibles
    #
    domainName = os.environ.get("DOMAIN")
    serverIp = os.environ.get("SERVERIP")
    zoneId = os.environ.get("ZONEID")
    #
    # Service function to update Route53
    #
    def r53_update_record(change):
        log.debug("{}: {}".format(change['action'],change['name'].to_text()))
        hostName = change['name'].to_text()
        resourceRecords = []
        # this probably can be better
        if change['type'] != 'SOA':
            if hostName == '@':
                hostName = ''
            elif hostName[-1] != '.':
                hostName += '.'
        for val in change['changeRec']:
            if change['type'] not in ['CNAME','SRV','MX','NS'] or val.to_text()[-1] == '.':
                log.debug("Changing {}".format(val.to_text()))
                resourceRecords.append({'Value': val.to_text()})
            else:
                log.debug("Changing {}".format(val.to_text() + '.' + domainName))
                resourceRecords.append({'Value':val.to_text() + '.' + domainName})

        returnVal = {
                    'Action': change['action'],
                    'ResourceRecordSet': {
                        'Name': hostName + domainName,
                        'Type': change['type'],
                        'ResourceRecords': resourceRecords,
                        'TTL': change['ttl']
                    }
        }
        return returnVal

    def make_action(chgs):
        retVal = {
            'Comment': 'Update by DNS mirror lambda',
            'Changes' : chgs
        }
        return retVal
        
    changes = diff_zones(zoneId = zoneId,domainName = domainName, serverIp = serverIp)
    updates = [r53_update_record(x) for x in changes]
    changeBatches = [make_action(x) for x in chunks(updates,500)]
    log.info("Applying {:d} change batches".format(len(changeBatches)))
    for b in changeBatches:

        r53Response = r53.change_resource_record_sets(HostedZoneId=zoneId,ChangeBatch=b)
        log.info("Changed {:d} records with changeId {}".format(len(b['Changes']),r53Response['ChangeInfo']['Id']))

    return "{:d} batches processed".format(len(changeBatches))
        
        


    
    
    
