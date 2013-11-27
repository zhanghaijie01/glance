# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 OpenStack Foundation
# Copyright 2013 IBM Corp.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import datetime

import kombu.entity
import mock
import mox
import qpid
import qpid.messaging
import stubout
import time
import webob

from glance.common import exception
import glance.context
from glance import notifier
from glance.notifier import notify_kombu
from glance.openstack.common import importutils, timeutils
import glance.openstack.common.log as logging
import glance.tests.unit.utils as unit_test_utils
from glance.tests import utils


DATETIME = datetime.datetime(2012, 5, 16, 15, 27, 36, 325355)


UUID1 = 'c80a1a6c-bd1f-41c5-90ee-81afedb1d58d'
USER1 = '54492ba0-f4df-4e4e-be62-27f4d76b29cf'
TENANT1 = '6838eb7b-6ded-434a-882c-b344c77fe8df'
TENANT2 = '2c014f32-55eb-467d-8fcb-4bd706012f81'


class ImageStub(glance.domain.Image):
    def get_data(self):
        return ['01234', '56789']

    def set_data(self, data, size=None):
        for chunk in data:
            pass


class ImageRepoStub(object):
    def remove(self, *args, **kwargs):
        return 'image_from_get'

    def save(self, *args, **kwargs):
        return 'image_from_save'

    def add(self, *args, **kwargs):
        return 'image_from_add'

    def get(self, *args, **kwargs):
        return 'image_from_get'

    def list(self, *args, **kwargs):
        return ['images_from_list']


class TaskStub(glance.domain.Task):
    def run(self):
        pass

    def succeed(self, result):
        pass

    def fail(self, message):
        pass


class TaskRepoStub(object):
    def remove(self, *args, **kwargs):
        return 'task_from_remove'

    def save(self, *args, **kwargs):
        return 'task_from_save'

    def add(self, *args, **kwargs):
        return 'task_from_add'

    def get(self, *args, **kwargs):
        return 'task_from_get'

    def list(self, *args, **kwargs):
        return ['tasks_from_list']


class TestNotifier(utils.BaseTestCase):

    def test_invalid_strategy(self):
        self.config(notifier_strategy="invalid_notifier")
        self.assertRaises(exception.InvalidNotifierStrategy,
                          notifier.Notifier)

    def test_custom_strategy(self):
        st = "glance.notifier.notify_noop.NoopStrategy"
        self.config(notifier_strategy=st)
        #NOTE(bcwaldon): the fact that Notifier is instantiated means we're ok
        notifier.Notifier()


class TestLoggingNotifier(utils.BaseTestCase):
    """Test the logging notifier is selected and works properly."""

    def setUp(self):
        super(TestLoggingNotifier, self).setUp()
        self.config(notifier_strategy="logging")
        self.called = False
        self.logger = logging.getLogger("glance.notifier.notify_log")
        self.notifier = notifier.Notifier()

    def _called(self, msg):
        self.called = msg

    def test_warn(self):
        self.logger.warn = self._called
        self.notifier.warn("test_event", "test_message")
        if self.called is False:
            self.fail("Did not call logging library correctly.")

    def test_info(self):
        self.logger.info = self._called
        self.notifier.info("test_event", "test_message")
        if self.called is False:
            self.fail("Did not call logging library correctly.")

    def test_erorr(self):
        self.logger.error = self._called
        self.notifier.error("test_event", "test_message")
        if self.called is False:
            self.fail("Did not call logging library correctly.")


class TestNoopNotifier(utils.BaseTestCase):
    """Test that the noop notifier works...and does nothing?"""

    def setUp(self):
        super(TestNoopNotifier, self).setUp()
        self.config(notifier_strategy="noop")
        self.notifier = notifier.Notifier()

    def test_warn(self):
        self.notifier.warn("test_event", "test_message")

    def test_info(self):
        self.notifier.info("test_event", "test_message")

    def test_error(self):
        self.notifier.error("test_event", "test_message")


class TestRabbitNotifier(utils.BaseTestCase):
    """Test AMQP/Rabbit notifier works."""

    def setUp(self):
        super(TestRabbitNotifier, self).setUp()

        def _fake_connect(rabbit_self):
            rabbit_self.connection_errors = ()
            rabbit_self.connection = 'fake_connection'
            return None

        self.notify_kombu = importutils.import_module("glance.notifier."
                                                      "notify_kombu")
        self.notify_kombu.RabbitStrategy._send_message = self._send_message
        self.notify_kombu.RabbitStrategy._connect = _fake_connect
        self.called = False
        self.config(notifier_strategy="rabbit",
                    rabbit_retry_backoff=0,
                    rabbit_notification_topic="fake_topic")
        self.notifier = notifier.Notifier()

    def _send_message(self, message, routing_key):
        self.called = {
            "message": message,
            "routing_key": routing_key,
        }

    def test_warn(self):
        self.notifier.warn("test_event", "test_message")

        if self.called is False:
            self.fail("Did not call _send_message properly.")

        self.assertEqual("test_message", self.called["message"]["payload"])
        self.assertEqual("WARN", self.called["message"]["priority"])
        self.assertEqual("fake_topic.warn", self.called["routing_key"])

    def test_info(self):
        self.notifier.info("test_event", "test_message")

        if self.called is False:
            self.fail("Did not call _send_message properly.")

        self.assertEqual("test_message", self.called["message"]["payload"])
        self.assertEqual("INFO", self.called["message"]["priority"])
        self.assertEqual("fake_topic.info", self.called["routing_key"])

    def test_error(self):
        self.notifier.error("test_event", "test_message")

        if self.called is False:
            self.fail("Did not call _send_message properly.")

        self.assertEqual("test_message", self.called["message"]["payload"])
        self.assertEqual("ERROR", self.called["message"]["priority"])
        self.assertEqual("fake_topic.error", self.called["routing_key"])

    def test_unknown_error_on_connect_raises(self):
        class MyException(Exception):
            pass

        def _connect(self):
            self.connection_errors = ()
            raise MyException('meow')

        self.notify_kombu.RabbitStrategy._connect = _connect
        self.assertRaises(MyException, notifier.Notifier)

    def test_timeout_on_connect_reconnects(self):
        info = {'num_called': 0}

        def _connect(rabbit_self):
            rabbit_self.connection_errors = ()
            info['num_called'] += 1
            if info['num_called'] == 1:
                raise Exception('foo timeout foo')
            rabbit_self.connection = 'fake_connection'

        self.notify_kombu.RabbitStrategy._connect = _connect
        notifier_ = notifier.Notifier()
        notifier_.error('test_event', 'test_message')

        if self.called is False:
            self.fail("Did not call _send_message properly.")

        self.assertEqual("test_message", self.called["message"]["payload"])
        self.assertEqual("ERROR", self.called["message"]["priority"])
        self.assertEqual(info['num_called'], 2)

    def test_connection_error_on_connect_reconnects(self):
        info = {'num_called': 0}

        class MyException(Exception):
            pass

        def _connect(rabbit_self):
            rabbit_self.connection_errors = (MyException, )
            info['num_called'] += 1
            if info['num_called'] == 1:
                raise MyException('meow')
            rabbit_self.connection = 'fake_connection'

        self.notify_kombu.RabbitStrategy._connect = _connect
        notifier_ = notifier.Notifier()
        notifier_.error('test_event', 'test_message')

        if self.called is False:
            self.fail("Did not call _send_message properly.")

        self.assertEqual("test_message", self.called["message"]["payload"])
        self.assertEqual("ERROR", self.called["message"]["priority"])
        self.assertEqual(info['num_called'], 2)

    def test_unknown_error_on_send_message_raises(self):
        class MyException(Exception):
            pass

        def _send_message(rabbit_self, msg, routing_key):
            raise MyException('meow')

        self.notify_kombu.RabbitStrategy._send_message = _send_message
        notifier_ = notifier.Notifier()
        self.assertRaises(MyException, notifier_.error, 'a', 'b')

    def test_timeout_on_send_message_reconnects(self):
        info = {'send_called': 0, 'conn_called': 0}

        def _connect(rabbit_self):
            info['conn_called'] += 1
            rabbit_self.connection_errors = ()
            rabbit_self.connection = 'fake_connection'

        def _send_message(rabbit_self, msg, routing_key):
            info['send_called'] += 1
            if info['send_called'] == 1:
                raise Exception('foo timeout foo')
            self._send_message(msg, routing_key)

        self.notify_kombu.RabbitStrategy._connect = _connect
        self.notify_kombu.RabbitStrategy._send_message = _send_message
        notifier_ = notifier.Notifier()
        notifier_.error('test_event', 'test_message')

        if self.called is False:
            self.fail("Did not call _send_message properly.")

        self.assertEqual("test_message", self.called["message"]["payload"])
        self.assertEqual("ERROR", self.called["message"]["priority"])
        self.assertEqual(info['send_called'], 2)
        self.assertEqual(info['conn_called'], 2)

    def test_connection_error_on_send_message_reconnects(self):
        info = {'send_called': 0, 'conn_called': 0}

        class MyException(Exception):
            pass

        def _connect(rabbit_self):
            info['conn_called'] += 1
            rabbit_self.connection_errors = (MyException, )
            rabbit_self.connection = 'fake_connection'

        def _send_message(rabbit_self, msg, routing_key):
            info['send_called'] += 1
            if info['send_called'] == 1:
                raise MyException('meow')
            self._send_message(msg, routing_key)

        self.notify_kombu.RabbitStrategy._connect = _connect
        self.notify_kombu.RabbitStrategy._send_message = _send_message
        notifier_ = notifier.Notifier()
        notifier_.error('test_event', 'test_message')

        if self.called is False:
            self.fail("Did not call _send_message properly.")

        self.assertEqual("test_message", self.called["message"]["payload"])
        self.assertEqual("ERROR", self.called["message"]["priority"])
        self.assertEqual(info['send_called'], 2)
        self.assertEqual(info['conn_called'], 2)


class TestQpidNotifier(utils.BaseTestCase):
    """Test Qpid notifier."""

    def setUp(self):
        super(TestQpidNotifier, self).setUp()

        self.mocker = mox.Mox()

        self.mock_connection = None
        self.mock_session = None
        self.mock_sender = None
        self.mock_receiver = None

        self.orig_connection = qpid.messaging.Connection
        self.orig_session = qpid.messaging.Session
        self.orig_sender = qpid.messaging.Sender
        self.orig_receiver = qpid.messaging.Receiver
        qpid.messaging.Connection = lambda *_x, **_y: self.mock_connection
        qpid.messaging.Session = lambda *_x, **_y: self.mock_session
        qpid.messaging.Sender = lambda *_x, **_y: self.mock_sender
        qpid.messaging.Receiver = lambda *_x, **_y: self.mock_receiver

        self.notify_qpid = importutils.import_module("glance.notifier."
                                                     "notify_qpid")
        self.addCleanup(self.reset_qpid)
        self.addCleanup(self.mocker.ResetAll)

    def reset_qpid(self):

        qpid.messaging.Connection = self.orig_connection
        qpid.messaging.Session = self.orig_session
        qpid.messaging.Sender = self.orig_sender
        qpid.messaging.Receiver = self.orig_receiver

    def _test_notify(self, priority, exception=False, exception_send=False):
        test_msg = {'a': 'b'}

        self.mock_connection = self.mocker.CreateMock(self.orig_connection)
        self.mock_session = self.mocker.CreateMock(self.orig_session)
        self.mock_sender = self.mocker.CreateMock(self.orig_sender)

        self.mock_connection.username = ""
        if exception:
            self.mock_connection.open().AndRaise(
                    Exception('Test open Exception'))
        else:
            self.mock_connection.open()
            self.mock_connection.session().AndReturn(self.mock_session)
            expected_address = ('glance/notifications.%s ; '
                                '{"node": {"x-declare": {"auto-delete": true, '
                                '"durable": false}, "type": "topic"}, '
                                '"create": "always"}' % priority)
            self.mock_session.sender(expected_address).AndReturn(
                    self.mock_sender)
            if exception_send:
                self.mock_sender.send(mox.IgnoreArg()).AndRaise(
                    Exception('Test send Exception'))
                # NOTE(afazekas): the opened and close call is expected
                # in this case, but not expected if the open fails
            else:
                self.mock_sender.send(mox.IgnoreArg())
            self.mock_connection.opened().AndReturn(True)
            self.mock_connection.close()

        self.mocker.ReplayAll()

        self.config(notifier_strategy="qpid")
        notifier = self.notify_qpid.QpidStrategy()
        if priority == 'info':
            if exception or exception_send:
                self.assertRaises(Exception, notifier.info, test_msg)
            else:
                notifier.info(test_msg)
        elif priority == 'warn':
            if exception or exception_send:
                self.assertRaises(Exception, notifier.warn, test_msg)
            else:
                notifier.warn(test_msg)
        elif priority == 'error':
            if exception or exception_send:
                self.assertRaises(Exception, notifier.error, test_msg)
            else:
                notifier.error(test_msg)

        self.mocker.VerifyAll()

    def test_info(self):
        self._test_notify('info')

    def test_warn(self):
        self._test_notify('warn')

    def test_error(self):
        self._test_notify('error')

    def test_exception_open_successful(self):
        self._test_notify('info', exception=True)

    def test_info_fail(self):
        self._test_notify('info', exception_send=True)

    def test_warn_fail(self):
        self._test_notify('warn', exception_send=True)

    def test_error_fail(self):
        self._test_notify('error', exception_send=True)


class TestRabbitContentType(utils.BaseTestCase):
    """Test AMQP/Rabbit notifier works."""

    def setUp(self):
        super(TestRabbitContentType, self).setUp()
        self.stubs = stubout.StubOutForTesting()

        def _fake_connect(rabbit_self):
            rabbit_self.connection_errors = ()
            rabbit_self.connection = 'fake_connection'
            rabbit_self.exchange = self._fake_exchange()
            return None

        def dummy(*args, **kwargs):
            pass

        self.stubs.Set(kombu.entity.Exchange, 'publish', dummy)
        self.stubs.Set(notify_kombu.RabbitStrategy, '_connect',
                       _fake_connect)
        self.called = False
        self.config(notifier_strategy="rabbit",
                    rabbit_retry_backoff=0,
                    rabbit_notification_topic="fake_topic")
        self.notifier = notifier.Notifier()

    def _fake_exchange(self):
        class Dummy(object):
            class Message(object):
                def __init__(message_self, message, content_type):
                    self.called = {
                        'message': message,
                        'content_type': content_type
                    }

            @classmethod
            def publish(*args, **kwargs):
                pass
        return Dummy

    def test_content_type_passed(self):
        self.notifier.warn("test_event", "test_message")
        self.assertEqual(self.called['content_type'], 'application/json')


class TestImageNotifications(utils.BaseTestCase):
    """Test Image Notifications work"""

    def setUp(self):
        super(TestImageNotifications, self).setUp()
        self.image = ImageStub(
                image_id=UUID1, name='image-1', status='active', size=1024,
                created_at=DATETIME, updated_at=DATETIME, owner=TENANT1,
                visibility='public', container_format='ami',
                tags=['one', 'two'], disk_format='ami', min_ram=128,
                min_disk=10, checksum='ca425b88f047ce8ec45ee90e813ada91',
                locations=['http://127.0.0.1'])
        self.context = glance.context.RequestContext(tenant=TENANT2,
                                                     user=USER1)
        self.image_repo_stub = ImageRepoStub()
        self.notifier = unit_test_utils.FakeNotifier()
        self.image_repo_proxy = glance.notifier.ImageRepoProxy(
                self.image_repo_stub, self.context, self.notifier)
        self.image_proxy = glance.notifier.ImageProxy(
                self.image, self.context, self.notifier)

    def test_image_save_notification(self):
        self.image_repo_proxy.save(self.image_proxy)
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)
        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'image.update')
        self.assertEqual(output_log['payload']['id'], self.image.image_id)
        if 'location' in output_log['payload']:
            self.fail('Notification contained location field.')

    def test_image_add_notification(self):
        self.image_repo_proxy.add(self.image_proxy)
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)
        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'image.create')
        self.assertEqual(output_log['payload']['id'], self.image.image_id)
        if 'location' in output_log['payload']:
            self.fail('Notification contained location field.')

    def test_image_delete_notification(self):
        self.image_repo_proxy.remove(self.image_proxy)
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)
        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'image.delete')
        self.assertEqual(output_log['payload']['id'], self.image.image_id)
        self.assertTrue(output_log['payload']['deleted'])
        if 'location' in output_log['payload']:
            self.fail('Notification contained location field.')

    def test_image_get(self):
        image = self.image_repo_proxy.get(UUID1)
        self.assertTrue(isinstance(image, glance.notifier.ImageProxy))
        self.assertEqual(image.image, 'image_from_get')

    def test_image_list(self):
        images = self.image_repo_proxy.list()
        self.assertTrue(isinstance(images[0], glance.notifier.ImageProxy))
        self.assertEqual(images[0].image, 'images_from_list')

    def test_image_get_data_notification(self):
        self.image_proxy.size = 10
        data = ''.join(self.image_proxy.get_data())
        self.assertEqual(data, '0123456789')
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)
        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'image.send')
        self.assertEqual(output_log['payload']['image_id'],
                         self.image.image_id)
        self.assertEqual(output_log['payload']['receiver_tenant_id'], TENANT2)
        self.assertEqual(output_log['payload']['receiver_user_id'], USER1)
        self.assertEqual(output_log['payload']['bytes_sent'], 10)
        self.assertEqual(output_log['payload']['owner_id'], TENANT1)

    def test_image_get_data_size_mismatch(self):
        self.image_proxy.size = 11
        list(self.image_proxy.get_data())
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)
        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'ERROR')
        self.assertEqual(output_log['event_type'], 'image.send')
        self.assertEqual(output_log['payload']['image_id'],
                         self.image.image_id)

    def test_image_set_data_prepare_notification(self):
        insurance = {'called': False}

        def data_iterator():
            output_logs = self.notifier.get_logs()
            self.assertEqual(len(output_logs), 1)
            output_log = output_logs[0]
            self.assertEqual(output_log['notification_type'], 'INFO')
            self.assertEqual(output_log['event_type'], 'image.prepare')
            self.assertEqual(output_log['payload']['id'], self.image.image_id)
            yield 'abcd'
            yield 'efgh'
            insurance['called'] = True

        self.image_proxy.set_data(data_iterator(), 8)
        self.assertTrue(insurance['called'])

    def test_image_set_data_upload_and_activate_notification(self):
        def data_iterator():
            self.notifier.log = []
            yield 'abcde'
            yield 'fghij'

        self.image_proxy.set_data(data_iterator(), 10)

        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 2)

        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'image.upload')
        self.assertEqual(output_log['payload']['id'], self.image.image_id)

        output_log = output_logs[1]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'image.activate')
        self.assertEqual(output_log['payload']['id'], self.image.image_id)

    def test_image_set_data_storage_full(self):
        def data_iterator():
            self.notifier.log = []
            yield 'abcde'
            raise exception.StorageFull('Modern Major General')

        self.assertRaises(webob.exc.HTTPRequestEntityTooLarge,
                          self.image_proxy.set_data, data_iterator(), 10)
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)

        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'ERROR')
        self.assertEqual(output_log['event_type'], 'image.upload')
        self.assertTrue('Modern Major General' in output_log['payload'])

    def test_image_set_data_value_error(self):
        def data_iterator():
            self.notifier.log = []
            yield 'abcde'
            raise ValueError('value wrong')

        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.image_proxy.set_data, data_iterator(), 10)

        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)

        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'ERROR')
        self.assertEqual(output_log['event_type'], 'image.upload')
        self.assertTrue('value wrong' in output_log['payload'])

    def test_image_set_data_duplicate(self):
        def data_iterator():
            self.notifier.log = []
            yield 'abcde'
            raise exception.Duplicate('Cant have duplicates')

        self.assertRaises(webob.exc.HTTPConflict,
                          self.image_proxy.set_data, data_iterator(), 10)

        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)

        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'ERROR')
        self.assertEqual(output_log['event_type'], 'image.upload')
        self.assertTrue('Cant have duplicates' in output_log['payload'])

    def test_image_set_data_storage_write_denied(self):
        def data_iterator():
            self.notifier.log = []
            yield 'abcde'
            raise exception.StorageWriteDenied('The Very Model')

        self.assertRaises(webob.exc.HTTPServiceUnavailable,
                          self.image_proxy.set_data, data_iterator(), 10)

        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)

        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'ERROR')
        self.assertEqual(output_log['event_type'], 'image.upload')
        self.assertTrue('The Very Model' in output_log['payload'])

    def test_image_set_data_forbidden(self):
        def data_iterator():
            self.notifier.log = []
            yield 'abcde'
            raise exception.Forbidden('Not allowed')

        self.assertRaises(webob.exc.HTTPForbidden,
                          self.image_proxy.set_data, data_iterator(), 10)

        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)

        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'ERROR')
        self.assertEqual(output_log['event_type'], 'image.upload')
        self.assertTrue('Not allowed' in output_log['payload'])

    def test_image_set_data_not_found(self):
        def data_iterator():
            self.notifier.log = []
            yield 'abcde'
            raise exception.NotFound('Not found')

        self.assertRaises(webob.exc.HTTPNotFound,
                          self.image_proxy.set_data, data_iterator(), 10)

        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)

        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'ERROR')
        self.assertEqual(output_log['event_type'], 'image.upload')
        self.assertTrue('Not found' in output_log['payload'])

    def test_image_set_data_HTTP_error(self):
        def data_iterator():
            self.notifier.log = []
            yield 'abcde'
            raise webob.exc.HTTPError('Http issue')

        self.assertRaises(webob.exc.HTTPError,
                          self.image_proxy.set_data, data_iterator(), 10)

        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)

        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'ERROR')
        self.assertEqual(output_log['event_type'], 'image.upload')
        self.assertTrue('Http issue' in output_log['payload'])

    def test_image_set_data_error(self):
        def data_iterator():
            self.notifier.log = []
            yield 'abcde'
            raise Exception('Failed')

        self.assertRaises(Exception,
                          self.image_proxy.set_data, data_iterator(), 10)

        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)

        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'ERROR')
        self.assertEqual(output_log['event_type'], 'image.upload')
        self.assertTrue('Failed' in output_log['payload'])


class RabbitStrategyTestCase(utils.BaseTestCase):
    def setUp(self):
        super(RabbitStrategyTestCase, self).setUp()
        self.rabbit_strategy = notify_kombu.RabbitStrategy()
        self.rabbit_strategy.retry_attempts = 0
        self.rabbit_strategy.max_retries = 2

    def test_close(self):
        self.rabbit_strategy.connection = kombu.connection.BrokerConnection()
        self.rabbit_strategy.connection.close = mock.Mock()
        self.rabbit_strategy._close()
        self.assertEqual(self.rabbit_strategy.connection, None)

    def test_connect(self):
        self.rabbit_strategy._close = mock.Mock()
        connection = kombu.connection.BrokerConnection(
            hostname='localhost',
            port=5672,
            userid='guest',
            password='guest',
            virtual_host='/',
            ssl=False)
        kombu.connection.BrokerConnection = mock.Mock()
        kombu.connection.BrokerConnection.return_value = connection
        connection.connect = mock.Mock()
        connection.channel = mock.Mock()
        connection.channel.return_value = 'fake_channel'
        kombu.entity.Exchange = mock.Mock()
        kombu.entity.Exchange.return_value = 'fake_exchange'
        fake_queue = mock.Mock()
        fake_queue.declare = mock.Mock()
        kombu.entity.Queue = mock.Mock()
        kombu.entity.Queue.return_value = fake_queue

        self.rabbit_strategy._connect()
        kombu.connection.BrokerConnection.assert_called_with(
            hostname='localhost',
            port=5672,
            userid='guest',
            password='guest',
            virtual_host='/',
            ssl=False)
        kombu.entity.Exchange.assert_called_with(
            channel='fake_channel',
            type='topic',
            durable=False,
            name='glance')
        for routing_key in ['notifications.warn', 'notifications.info',
                            'notifications.error']:
            kombu.entity.Queue.assert_any_called(
                channel='fake_channel',
                exchange='fake_exchange',
                durable=False,
                name=routing_key,
                routing_key=routing_key)

    def test_reconnect_sleep_time(self):
        self.rabbit_strategy._connect = mock.Mock(
            side_effect=Exception('timeout'))
        time.sleep = mock.Mock()
        try:
            self.rabbit_strategy.reconnect()
        except notify_kombu.KombuMaxRetriesReached:
            pass
        finally:
            time.sleep.assert_called_once_with(2)

    def test_reconnect_sleep_time_2(self):
        self.rabbit_strategy.retry_backoff = 40
        self.rabbit_strategy._connect = mock.Mock(
            side_effect=Exception('timeout'))
        time.sleep = mock.Mock()
        try:
            self.rabbit_strategy.reconnect()
        except notify_kombu.KombuMaxRetriesReached:
            pass
        finally:
            time.sleep.assert_called_once_with(30)

    def test_reconnect_sleep_time_no_retry_max_backoff(self):
        self.rabbit_strategy.retry_max_backoff = None
        self.rabbit_strategy.retry_backoff = 100
        self.rabbit_strategy._connect = mock.Mock(
            side_effect=Exception('timeout'))
        time.sleep = mock.Mock()
        try:
            self.rabbit_strategy.reconnect()
        except notify_kombu.KombuMaxRetriesReached:
            pass
        finally:
            time.sleep.assert_called_once_with(100)

    def test_notify_process_komby_max_retries_reached_error(self):
        self.rabbit_strategy.connection = None
        self.rabbit_strategy.reconnect = mock.Mock(
            side_effect=notify_kombu.KombuMaxRetriesReached())
        self.rabbit_strategy.log_failure = mock.Mock()

        self.rabbit_strategy._notify('fake_msg', "WARN")
        self.rabbit_strategy.log_failure.assert_called_with('fake_msg', "WARN")

    def test_notify_check_if_log_failure(self):
        self.rabbit_strategy.connection = 'fake_connection'
        self.rabbit_strategy._send_message = mock.Mock(
            side_effect=Exception('timeout'))
        self.rabbit_strategy.reconnect = mock.Mock(
            side_effect=notify_kombu.KombuMaxRetriesReached())
        self.rabbit_strategy.log_failure = mock.Mock()

        self.rabbit_strategy._notify('fake_msg', "WARN")
        self.rabbit_strategy._send_message. \
            assert_called_with('fake_msg', 'notifications.warn')
        self.rabbit_strategy.log_failure.assert_called_with('fake_msg', "WARN")


class TestTaskNotifications(utils.BaseTestCase):
    """Test Task Notifications work"""

    def setUp(self):
        super(TestTaskNotifications, self).setUp()
        self.task = TaskStub(
            task_id='aaa',
            type='import',
            status='pending',
            input={"loc": "fake"},
            result='',
            owner=TENANT2,
            message='',
            expires_at=None,
            created_at=DATETIME,
            updated_at=DATETIME
        )
        self.context = glance.context.RequestContext(
            tenant=TENANT2,
            user=USER1
        )
        self.task_repo_stub = TaskRepoStub()
        self.notifier = unit_test_utils.FakeNotifier()
        self.task_repo_proxy = glance.notifier.TaskRepoProxy(
            self.task_repo_stub,
            self.context,
            self.notifier
        )
        self.task_proxy = glance.notifier.TaskProxy(
            self.task,
            self.context,
            self.notifier
        )
        timeutils.set_time_override()

    def tearDown(self):
        super(TestTaskNotifications, self).tearDown()
        timeutils.clear_time_override()

    def test_task_create_notification(self):
        self.task_repo_proxy.add(self.task_proxy)
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)
        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'task.create')
        self.assertEqual(output_log['payload']['id'], self.task.task_id)
        self.assertEqual(
            output_log['payload']['updated_at'],
            timeutils.isotime(self.task.updated_at)
        )
        self.assertEqual(
            output_log['payload']['created_at'],
            timeutils.isotime(self.task.created_at)
        )
        if 'location' in output_log['payload']:
            self.fail('Notification contained location field.')

    def test_task_delete_notification(self):
        now = timeutils.isotime()
        self.task_repo_proxy.remove(self.task_proxy)
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)
        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'task.delete')
        self.assertEqual(output_log['payload']['id'], self.task.task_id)
        self.assertEqual(
            output_log['payload']['updated_at'],
            timeutils.isotime(self.task.updated_at)
        )
        self.assertEqual(
            output_log['payload']['created_at'],
            timeutils.isotime(self.task.created_at)
        )
        self.assertEqual(
            output_log['payload']['deleted_at'],
            now
        )
        if 'location' in output_log['payload']:
            self.fail('Notification contained location field.')

    def test_task_run_notification(self):
        self.assertRaises(
            NotImplementedError,
            self.task_proxy.run,
            executor=None
        )
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)
        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'task.run')
        self.assertEqual(output_log['payload']['id'], self.task.task_id)

    def test_task_processing_notification(self):
        self.task_proxy.begin_processing()
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)
        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'task.processing')
        self.assertEqual(output_log['payload']['id'], self.task.task_id)

    def test_task_success_notification(self):
        self.task_proxy.begin_processing()
        self.task_proxy.succeed(result=None)
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 2)
        output_log = output_logs[1]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'task.success')
        self.assertEqual(output_log['payload']['id'], self.task.task_id)

    def test_task_failure_notification(self):
        self.task_proxy.fail(message=None)
        output_logs = self.notifier.get_logs()
        self.assertEqual(len(output_logs), 1)
        output_log = output_logs[0]
        self.assertEqual(output_log['notification_type'], 'INFO')
        self.assertEqual(output_log['event_type'], 'task.failure')
        self.assertEqual(output_log['payload']['id'], self.task.task_id)
