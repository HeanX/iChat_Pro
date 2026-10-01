from django.test import TestCase


class HealthEndpointTests(TestCase):
    def test_liveness_returns_ok(self):
        response = self.client.get('/health/live/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_readiness_returns_ok_when_dependencies_reachable(self):
        response = self.client.get('/health/ready/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_health_endpoints_reject_post(self):
        self.assertEqual(self.client.post('/health/live/').status_code, 405)
        self.assertEqual(self.client.post('/health/ready/').status_code, 405)
