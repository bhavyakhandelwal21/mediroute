import tempfile
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from fastapi import HTTPException
from app import blood

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def position(i, target=10, **kw):
    return dict(id=i, facility=f'Hospital {i}', blood_group='A+', component='Red cells',
                target_units=target, storage_verified=True, transport_verified=True, **kw)


def batch(i, owner, units, hours=48, released=True):
    return dict(id=i, position_id=owner, batch_code=f'B{i}', units=units,
                expires_at=(NOW+timedelta(hours=hours)).isoformat(), released=released)


class PlannerTests(unittest.TestCase):
    def test_zero_stock_receiver_and_donor_target(self):
        result = blood.analyse([position(1), position(2)], [batch(1, 1, 25)], now=NOW)
        self.assertEqual(result['transfers'][0]['units'], 10)
        self.assertEqual(result['rows'][0]['proposed_out'], 10)
        self.assertEqual(result['summary']['unmet_units'], 0)

    def test_no_group_rh_or_component_substitution(self):
        for key, value in [('blood_group', 'O+'), ('blood_group', 'A-'), ('component', 'Platelets')]:
            receiver = position(2)
            receiver[key] = value
            self.assertFalse(blood.analyse([position(1), receiver], [batch(1, 1, 25)], now=NOW)['transfers'])

    def test_expired_unreleased_and_arrival_expiry_excluded(self):
        lots = [batch(1, 1, 30, -1), batch(2, 1, 30, released=False), batch(3, 1, 30, 2)]
        result = blood.analyse([position(1), position(2)], lots, transit_hours=2, now=NOW)
        self.assertFalse(result['transfers'])
        self.assertEqual(result['rows'][0]['usable_units'], 30)

    def test_handling_blocks_transfers(self):
        for index, key in [(0, 'storage_verified'), (0, 'transport_verified'), (1, 'storage_verified')]:
            positions = [position(1), position(2)]
            positions[index][key] = False
            self.assertFalse(blood.analyse(positions, [batch(1, 1, 25)], now=NOW)['transfers'])

    def test_multiple_receivers_cannot_double_allocate(self):
        result = blood.analyse([position(1), position(2), position(3)], [batch(1, 1, 25)], now=NOW)
        self.assertEqual(sum(t['units'] for t in result['transfers']), 15)
        self.assertEqual(result['summary']['unmet_units'], 5)

    def test_multiple_donors_and_earliest_expiry(self):
        lots = [batch(1, 1, 14, 48), batch(2, 2, 14, 24)]
        result = blood.analyse([position(1), position(2), position(3)], lots, now=NOW)
        self.assertEqual(result['transfers'][0]['batch_id'], 2)
        self.assertEqual(result['summary']['transfer_units'], 8)
        self.assertEqual(result['summary']['unmet_units'], 2)

    def test_soon_expiring_stock_does_not_protect_donor_reserve(self):
        result = blood.analyse([position(1), position(2)], [batch(1, 1, 10, 1), batch(2, 1, 12)], now=NOW)
        self.assertEqual(result['summary']['transfer_units'], 2)

    def test_same_hospital_cannot_donate_to_itself(self):
        first, second = position(1), position(2)
        second['facility'] = first['facility'].upper()
        self.assertFalse(blood.analyse([first, second], [batch(1, 1, 25)], now=NOW)['transfers'])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='blood-test-', dir=Path(__file__).resolve().parent)
        self.patch = patch.object(blood.db, 'DB', Path(self.tmp.name)/'blood.db')
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_updates_persist_without_duplicating_stock(self):
        p = blood.Position(facility=' Test  Hospital ', blood_group='A+', component='Red cells', target_units=10,
                           storage_verified=True, transport_verified=True)
        saved = blood.save_position(p)
        p.facility = 'test hospital'
        self.assertEqual(saved['id'], blood.save_position(p)['id'])
        b = blood.Batch(position_id=saved['id'], batch_code='B1', units=20,
                        expires_at=datetime.now(timezone.utc)+timedelta(days=2), released=True)
        blood.save_batch(b)
        b.units = 18
        blood.save_batch(b)
        result = blood.analysis(2)
        self.assertEqual(result['rows'][0]['usable_units'], 18)
        self.assertEqual(len(result['rows'][0]['batches']), 1)
        blood.delete_batch(result['rows'][0]['batches'][0]['id'])
        self.assertEqual(blood.analysis(2)['rows'][0]['usable_units'], 0)

    def test_invalid_inputs_and_unknown_positions(self):
        for data in [dict(facility=' ', target_units=1), dict(facility='X', target_units=-1), dict(facility='X', target_units=1.5)]:
            with self.assertRaises(ValidationError):
                blood.Position(blood_group='A+', component='Red cells', **data)
        with self.assertRaises(ValidationError):
            blood.Position(facility='X', blood_group='A', component='Red cells', target_units=1)
        with self.assertRaises(ValidationError):
            blood.Batch(position_id=1, batch_code='B', units=2, expires_at='2026-10-22T12:00:00')
        with self.assertRaises(HTTPException):
            blood.save_batch(blood.Batch(position_id=999, batch_code='B', units=2, expires_at=NOW))

    def test_medicine_clear_preserves_blood(self):
        blood.save_position(blood.Position(facility='Hospital', blood_group='A+', component='Red cells', target_units=10))
        c = blood.db.conn()
        blood.db.clear(c)
        c.close()
        self.assertEqual(len(blood.analysis(2)['rows']), 1)

    @unittest.skipUnless(os.getenv('MEDIROUTE_HTTP_TESTS') == '1', 'Opt-in HTTP check requires host loopback sockets')
    def test_http_workflow_and_validation(self):
        from fastapi.testclient import TestClient
        from app.main import app
        # Avoid application startup's medicine sample seeding; use isolated data.
        client = TestClient(app)
        try:
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(len(client.get('/api/blood/catalog').json()['groups']), 8)
            payload = dict(facility='North', blood_group='A+', component='Red cells', target_units=10,
                           storage_verified=True, transport_verified=True)
            r = client.post('/api/blood/positions', json=payload)
            self.assertEqual(r.status_code, 200, r.text)
            donor = r.json()['id']
            payload['facility'] = 'South'
            self.assertEqual(client.post('/api/blood/positions', json=payload).status_code, 200)
            lot = dict(position_id=donor, batch_code='RBC-001', units=25, released=True,
                       expires_at=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat())
            self.assertEqual(client.post('/api/blood/batches', json=lot).status_code, 200)
            r = client.get('/api/blood/analysis?transit_hours=2')
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['summary']['transfer_units'], 10)
            self.assertEqual(r.json()['transfers'][0]['to'], 'South')
            self.assertEqual(client.get('/api/blood/analysis?transit_hours=-1').status_code, 422)
            self.assertEqual(client.get('/api/blood/analysis?transit_hours=nan').status_code, 422)
            lot['units'] = -3
            self.assertEqual(client.post('/api/blood/batches', json=lot).status_code, 422)
            self.assertEqual(client.delete('/api/blood/batches/99999').status_code, 404)
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main()
