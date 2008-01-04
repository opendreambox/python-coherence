# Licensed under the MIT license
# http://opensource.org/licenses/mit-license.php

# Copyright 2007 - Frank Scholz <coherence@beebits.net>

from twisted.web2 import http, http_headers, resource
from twisted.python import log, failure
from twisted.internet import defer

from coherence import log, SERVER_ID

from coherence.extern.et import ET, namespace_map_update

from coherence.upnp.core.utils import parse_xml

from coherence.upnp.core import soap_lite

class errorCode(Exception):
    def __init__(self, status):
        Exception.__init__(self)
        self.status = status

class UPnPPublisher(resource.Resource, log.Loggable):
    """ Based upon twisted.web.soap.SOAPPublisher and
        extracted to remove the SOAPpy dependency

        UPnP requires headers and OUT parameters to be returned
        in a slightly
        different way than the SOAPPublisher class does.
    """
    logCategory = 'soap'
    isLeaf = 1
    encoding = "UTF-8"
    envelope_attrib = None

    def _sendResponse(self, request, response, status=200):
        self.info('_sendResponse', status, response)

        if status == 200:
            response_code = 200
        else:
            response_code = 500

        response_headers = http_headers.Headers()
        if self.encoding is not None:
            mimeType = 'text/xml; charset="%s"' % self.encoding
        else:
            mimeType = "text/xml"
        response_headers.setRawHeaders('Content-type', [mimeType])
        response_headers.setRawHeaders('Content-length', [str(len(response))])
        response_headers.setRawHeaders('EXT', [''])
        response_headers.setRawHeaders('SERVER', [SERVER_ID])
        return http.Response(response_code,
                             response_headers,
                             response)

    def _methodNotFound(self, request, methodName):
        response = soap_lite.build_soap_error(401)
        return self._sendResponse(request, response, status=401)

    def _gotResult(self, result, request, methodName, ns):
        self.info('_gotResult', result, request, methodName, ns)

        response = soap_lite.build_soap_call("{%s}%s" % (ns, methodName), result,
                                                is_response=True,
                                                encoding=None)
        #print "SOAP-lite response", response
        return self._sendResponse(request, response)

    def _gotError(self, failure, request, methodName, ns):
        self.info('_gotError', failure, failure.value)
        e = failure.value
        status = 500

        if isinstance(e, errorCode):
            status = e.status
        else:
            failure.printTraceback()

        response = soap_lite.build_soap_error(status)
        return self._sendResponse(request, response, status=status)

    def lookupFunction(self, functionName):
        function = getattr(self, "soap_%s" % functionName, None)
        if not function:
            function = getattr(self, "soap__generic", None)
        if function:
            return function, getattr(function, "useKeywords", False)
        else:
            return None, None

    def http_POST(self, request):
        """Handle a SOAP command."""

        def got_data(data):
            headers = request.headers
            self.info('soap_request:', headers)

            def print_c(e):
                for c in e.getchildren():
                    print c, c.tag
                    print_c(c)

            tree = parse_xml(data)
            #root = tree.getroot()
            #print_c(root)

            body = tree.find('{http://schemas.xmlsoap.org/soap/envelope/}Body')
            method = body.getchildren()[0]
            methodName = method.tag
            ns = None

            if methodName.startswith('{') and methodName.rfind('}') > 1:
                ns, methodName = methodName[1:].split('}')

            args = []
            kwargs = {}
            for child in method.getchildren():
                kwargs[child.tag] = self.decode_result(child)
                args.append(kwargs[child.tag])

            #p, header, body, attrs = SOAPpy.parseSOAPRPC(data, 1, 1, 1)
            #methodName, args, kwargs, ns = p._name, p._aslist, p._asdict, p._ns

            try:
                content_type = headers.getRawHeaders('content-type')
                content_type[0].index('text/xml')
            except:
                return self._gotError(failure.Failure(errorCode(415)), request, methodName, ns)

            self.debug('headers: %r' % headers)

            function, useKeywords = self.lookupFunction(methodName)
            #print 'function', function, 'keywords', useKeywords, 'args', args, 'kwargs', kwargs

            if not function:
                return self._methodNotFound(request, methodName)
            else:
                keywords = {'soap_methodName':methodName}
                client = headers.getRawHeaders('user-agent')
                if(client is not None and
                        client[0].find('Xbox/') == 0):
                    keywords['X_UPnPClient'] = 'XBox'
                client = headers.getRawHeaders('x-av-client-info')
                if(client is not None and
                        client[0].find('"PLAYSTATION3') > 0):
                    keywords['X_UPnPClient'] = 'PLAYSTATION3'

                for k, v in kwargs.items():
                    keywords[str(k)] = v
                self.info('call', methodName, keywords)
                if hasattr(function, "useKeywords"):
                    d = defer.maybeDeferred(function, **keywords)
                else:
                    d = defer.maybeDeferred(function, *args, **keywords)

                d.addCallback(self._gotResult, request, methodName, ns)
                d.addErrback(self._gotError, request, methodName, ns)
                return d

        d = request.stream.read()
        d.addCallback(got_data)
        return d

    def decode_result(self, element):
        type = element.get('{http://www.w3.org/1999/XMLSchema-instance}type')
        if type is not None:
            try:
                prefix, local = type.split(":")
                if prefix == 'xsd':
                    type = local
            except ValueError:
                pass

        if type == "integer" or type == "int":
            return int(element.text)
        if type == "float" or type == "double":
            return float(element.text)
        if type == "boolean":
            return element.text == "true"

        return element.text or ""
