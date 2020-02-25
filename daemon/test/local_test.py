# ovirt-imageio
# Copyright (C) 2015-2018 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from __future__ import absolute_import
from __future__ import print_function

import io
import json

import pytest
from six.moves import http_client

from ovirt_imageio import auth
from ovirt_imageio import config
from ovirt_imageio import configloader
from ovirt_imageio import services

from . import http
from . import testutil

from . marks import requires_python3

pytestmark = requires_python3


@pytest.fixture(scope="module")
def service():
    configloader.load(config, ["test/conf/daemon/conf"])
    s = services.LocalService(config)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def setup_function(f):
    auth.clear()


def test_method_not_allowed(service):
    with http.UnixClient(service.address) as c:
        res = c.request("FOO", "/images/")
        assert res.status == http_client.METHOD_NOT_ALLOWED


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH"])
def test_no_resource(service, method):
    with http.UnixClient(service.address) as c:
        res = c.request(method, "/no/resource")
        assert res.status == http_client.NOT_FOUND


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH"])
def test_no_ticket_id(service, method):
    with http.UnixClient(service.address) as c:
        res = c.request(method, "/images/")
        assert res.status == http_client.BAD_REQUEST


@pytest.mark.parametrize("method,body", [
    ("GET", None),
    ("PUT", "body"),
    ("PATCH", json.dumps({"op": "flush"}).encode("ascii")),
])
def test_no_ticket(service, method, body):
    with http.UnixClient(service.address) as c:
        res = c.request(method, "/images/no-ticket", body=body)
        assert res.status == http_client.FORBIDDEN


def test_put_forbidden(service):
    ticket = testutil.create_ticket(
        url="file:///no/such/image", ops=["read"])
    auth.add(ticket)
    with http.UnixClient(service.address) as c:
        res = c.request("PUT", "/images/" + ticket["uuid"], "content")
        assert res.status == http_client.FORBIDDEN


def test_put(service, tmpdir):
    data = b"-------|after"
    image = testutil.create_tempfile(tmpdir, "image", data)
    ticket = testutil.create_ticket(url="file://" + str(image))
    auth.add(ticket)
    uri = "/images/" + ticket["uuid"]
    with http.UnixClient(service.address) as c:
        res = c.request("PUT", uri, "content")
        assert res.status == http_client.OK
        assert res.getheader("content-length") == "0"
        with io.open(str(image)) as f:
            assert f.read(len(data)) == "content|after"


def test_get_forbidden(service):
    ticket = testutil.create_ticket(
        url="file:///no/such/image", ops=[])
    auth.add(ticket)
    with http.UnixClient(service.address) as c:
        res = c.request("GET", "/images/" + ticket["uuid"], "content")
        assert res.status == http_client.FORBIDDEN


def test_get(service, tmpdir):
    data = b"a" * 512 + b"b" * 512
    image = testutil.create_tempfile(tmpdir, "image", data)
    ticket = testutil.create_ticket(
        url="file://" + str(image), size=1024)
    auth.add(ticket)
    with http.UnixClient(service.address) as c:
        res = c.request("GET", "/images/" + ticket["uuid"])
        assert res.status == http_client.OK
        assert res.read() == data


def test_images_zero(service, tmpdir):
    data = b"x" * 512
    image = testutil.create_tempfile(tmpdir, "image", data)
    ticket = testutil.create_ticket(url="file://" + str(image))
    auth.add(ticket)
    msg = {"op": "zero", "size": 20, "offset": 10, "future": True}
    size = msg["size"]
    offset = msg.get("offset", 0)
    body = json.dumps(msg).encode("ascii")
    with http.UnixClient(service.address) as c:
        res = c.request("PATCH", "/images/" + ticket["uuid"], body)
        assert res.status == http_client.OK
        assert res.getheader("content-length") == "0"
        with io.open(str(image), "rb") as f:
            assert f.read(offset) == data[:offset]
            assert f.read(size) == b"\0" * size
            assert f.read() == data[offset + size:]


def test_options(service):
    with http.UnixClient(service.address) as c:
        res = c.request("OPTIONS", "/images/*")
        allows = {"OPTIONS", "GET", "PUT", "PATCH"}
        features = {"zero", "flush", "extents"}
        assert res.status == http_client.OK
        assert set(res.getheader("allow").split(',')) == allows
        options = json.loads(res.read())
        assert set(options["features"]) == features
        assert options["unix_socket"] == service.address
