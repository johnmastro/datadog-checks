from datetime import datetime
import requests

from checks import AgentCheck
from util import headers
import sys


class CloudantCheck(AgentCheck):
    """Extracts stats from Cloudant via its REST API
    https://docs.cloudant.com/monitoring.html
    """

    SERVICE_CHECK_NAME = 'cloudant.can_connect'
    SOURCE_TYPE_NAME = 'cloudant'
    TIMEOUT = 5
    URL_TEMPLATE = 'https://{username}.cloudant.com/_api/v2/monitoring/{endpoint}?cluster={cluster}'

    def __init__(self, name, init_config, agentConfig, instances=None):
        super(CloudantCheck, self).__init__(name, init_config, agentConfig, instances)
        self.last_timestamps = {}

    def _validate_instance(self, instance):
        for key in ['cluster', 'username', 'password']:
            if not key in instance:
                raise Exception("A {} must be specified".format(key))

    def _build_url(self, endpoint, instance):
        return self.URL_TEMPLATE.format(
            endpoint=endpoint,
            **instance
        )

    def _get_stats(self, url, instance):
        "Hit a given URL and return the parsed json"
        self.log.debug('Fetching Cloudant stats at url: %s' % url)

        auth = (instance['username'], instance['password'])
        # Override Accept request header so that failures are not redirected to the Futon web-ui
        request_headers = headers(self.agentConfig)
        request_headers['Accept'] = 'text/json'
        r = requests.get(url, auth=auth, headers=request_headers,
                         timeout=int(instance.get('timeout', self.TIMEOUT)))
        r.raise_for_status()
        return r.json()

    def check(self, instance):
        self._validate_instance(instance)

        tags = instance.get('tags', [])
        tags.append('cluster:{}'.format(instance['cluster']))
        self.check_connection(instance, tags)

        self.get_status_code_data(instance, tags)

    def check_connection(self, instance, tags):
        url = self._build_url('uptime', instance)
        try:
            self._get_stats(url, instance)
        except requests.exceptions.Timeout as e:
            self.service_check(self.SERVICE_CHECK_NAME, AgentCheck.CRITICAL,
                tags=tags, message="Request timeout: {0}, {1}".format(url, e))
            raise
        except requests.exceptions.HTTPError as e:
            self.service_check(self.SERVICE_CHECK_NAME, AgentCheck.CRITICAL,
                tags=tags, message=str(e.message))
            raise
        except Exception as e:
            self.service_check(self.SERVICE_CHECK_NAME, AgentCheck.CRITICAL,
                tags=tags, message=str(e))
            raise
        else:
            self.service_check(self.SERVICE_CHECK_NAME, AgentCheck.OK,
                tags=tags,
                message='Connection to %s was successful' % url)

    def get_status_code_data(self, instance, tags):
        endpoint = 'rate/status_code'

        url = self._build_url(endpoint, instance)

        # Fetch initial stats and capture a service check based on response.
        service_check_tags = ['cluster:{}'.format(instance['cluster'])]
        try:
            data = self._get_stats(url, instance)
        except requests.exceptions.HTTPError as e:
            self.warning('Error reading data from URL: {}'.format(url))
            return

        # No overall stats? bail out now
        if data is None:
            self.warning("No stats could be retrieved from {}".format(url))

        self.record_data(data, 'status_code', lambda target: target.split(' ', 1)[-1], tags)

    def _should_record_data(self, tag, epoch):
        last_ts = self.last_timestamps.get(tag, None)
        return not last_ts or last_ts < epoch

    def record_data(self, data, endpoint_name, stat_name_fn, tags=None):
        end_epoch = data['end']
        prefix = '.'.join(['cloudant', endpoint_name])
        if not self._should_record_data(prefix, end_epoch):
            self.log.info('Skipping old data: {}'.format(prefix))
            return

        for response in data['target_responses']:
            target = response['target']
            metric_name = '.'.join([prefix, stat_name_fn(target)])
            datapoints = response['datapoints']
            for datapoint in datapoints:
                value, epoch = datapoint
                if value is not None and self._should_record_data(metric_name, epoch):
                    self.log.info('Recording data: {}, {}'.format(metric_name, value))
                    self.last_timestamps[metric_name] = epoch
                    metric_tags = tags or []
                    self.gauge(metric_name, value, tags=metric_tags, timestamp=epoch)


if __name__ == '__main__':
    if len(sys.argv) == 2:
        path = sys.argv[1]
    else:
        print "Usage: python cloudant.py <path_to_config>"
    check, instances = CloudantCheck.from_yaml(path)
    for instance in instances:
        print "\nRunning the check against cluster: %s" % (instance['cluster'])
        check.check(instance)
        if check.has_events():
            print 'Events: %s' % (check.get_events())
        print 'Metrics: %s' % (check.get_metrics())
