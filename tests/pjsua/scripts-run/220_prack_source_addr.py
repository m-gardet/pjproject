import inc_const as const
import inc_sip as sip
from inc_cfg import InstanceParam, TestError, TestParam


def check_prack(test):
    for mode, with_route in [(0, False), (1, False), (1, True)]:
        use_source = mode != 0
        caller = test.process[mode]
        server = sip.Dialog("127.0.0.1", caller.inst_param.sip_port)
        contact = sip.Dialog("127.0.0.1", caller.inst_param.sip_port)
        try:
            caller.send("call new sip:127.0.0.1:%d" % server.local_port)
            invite, source = server.wait_msg_from(10)
            if not invite.startswith("INVITE "):
                raise TestError("Expected INVITE at the simulated server")

            host = "unresolvable.invalid" if use_source else "127.0.0.1"
            target = "sip:callee@%s:%d" % (host, contact.local_port)
            response = server.create_response(invite, 183, "Progress",
                                              to_tag="prack-source")
            response += "Contact: <%s>\r\n" % target
            if with_route:
                response += ("Record-Route: <sip:route.invalid:5099;lr>\r\n")
            response += "Require: 100rel\r\nRSeq: 1\r\n"
            response += "Content-Length: 0\r\n\r\n"
            server.send_msg(response, source)

            receiver = server if use_source else contact
            prack, prack_source = receiver.wait_msg_from(10)
            if not prack.startswith("PRACK %s SIP/2.0" % target):
                raise TestError("PRACK missing or Request-URI changed")
            cseq = sip.get_header(invite, "CSeq").split()[0]
            if sip.get_header(prack, "RAck") != "1 %s INVITE" % cseq:
                raise TestError("Incorrect RAck header")
            if with_route and "<sip:route.invalid:5099;lr>" not in prack:
                raise TestError("PRACK Route header was not preserved")

            ok = receiver.create_response(prack, 200, "OK")
            receiver.send_msg(ok + "Content-Length: 0\r\n\r\n",
                              prack_source)
            final = server.create_response(invite, 486, "Busy Here",
                                           to_tag="prack-source")
            server.send_msg(final + "Content-Length: 0\r\n\r\n", source)
            caller.expect(const.STATE_DISCONNECTED)
            caller.sync_stdout()
        finally:
            server.sock.close()
            contact.sock.close()


SDP = ("v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=source-routing\r\n"
       "c=IN IP4 127.0.0.1\r\nt=0 0\r\nm=audio 9 RTP/AVP 0\r\n"
       "a=rtpmap:0 PCMU/8000\r\na=inactive\r\n")


def check_dialog(test, with_route):
    caller = test.process[1]
    server = sip.Dialog("127.0.0.1", caller.inst_param.sip_port)
    final_server = sip.Dialog("127.0.0.1", caller.inst_param.sip_port)
    refresh_server = sip.Dialog("127.0.0.1", caller.inst_param.sip_port)
    target = "sip:callee@unresolvable.invalid:5099"
    route = "Record-Route: <sip:route.invalid:5099;lr>\r\n" if with_route else ""
    try:
        caller.send("call new sip:127.0.0.1:%d" % server.local_port)
        invite, source = server.wait_msg_from(10)
        if not invite.startswith("INVITE "):
            raise TestError("Expected initial INVITE")

        provisional = server.create_response(invite, 183, "Progress",
                                              to_tag="contact-source")
        provisional += "Contact: <%s>\r\n" % target
        provisional += route + "Require: 100rel\r\nRSeq: 1\r\n"
        server.send_msg(provisional + "Content-Length: 0\r\n\r\n", source)
        prack, prack_source = server.wait_msg_from(10)
        if not prack.startswith("PRACK %s SIP/2.0" % target):
            raise TestError("PRACK did not reach the provisional response source")
        response = server.create_response(prack, 200, "OK")
        server.send_msg(response + "Content-Length: 0\r\n\r\n", prack_source)

        final = final_server.create_response(invite, 200, "OK",
                                             to_tag="contact-source")
        final += "Contact: <%s>\r\n" % target
        final += route + "Content-Type: application/sdp\r\n"
        final += "Content-Length: %d\r\n\r\n%s" % (len(SDP), SDP)
        final_server.send_msg(final, source)
        ack, unused = final_server.wait_msg_from(10)
        if not ack.startswith("ACK %s SIP/2.0" % target):
            raise TestError("ACK did not reach the final response source")
        caller.expect(const.STATE_CONFIRMED)

        final_server.send_msg(final, source)
        ack, unused = final_server.wait_msg_from(10)
        if not ack.startswith("ACK %s SIP/2.0" % target):
            raise TestError("Retransmitted 200 was not acknowledged")

        update = "UPDATE sip:127.0.0.1:%d SIP/2.0\r\n" % caller.inst_param.sip_port
        update += ("Via: SIP/2.0/UDP 127.0.0.1:%d;"
               "branch=z9hG4bKsource%d;rport\r\n"
               % (refresh_server.local_port, refresh_server.local_port))
        update += "From: %s\r\n" % sip.get_header(final, "To")
        update += "To: %s\r\n" % sip.get_header(invite, "From")
        update += "Call-ID: %s\r\n" % sip.get_header(invite, "Call-ID")
        update += "CSeq: 123 UPDATE\r\nMax-Forwards: 70\r\n"
        update += "Contact: <%s>\r\nContent-Length: 0\r\n\r\n" % target
        refresh_server.send_msg(update, source)
        response, unused = refresh_server.wait_msg_from(10)
        if sip.get_code(response) != 200:
            raise TestError("Contact source refresh failed")

        caller.send("call update")
        update, update_source = refresh_server.wait_msg_from(10)
        if not update.startswith("UPDATE %s SIP/2.0" % target):
            raise TestError("Outgoing UPDATE did not use the refreshed source")
        response = refresh_server.create_response(update, 200, "OK")
        response += "Content-Type: application/sdp\r\n"
        response += "Content-Length: %d\r\n\r\n%s" % (len(SDP), SDP)
        refresh_server.send_msg(response, update_source)
        caller.sync_stdout()

        caller.send("call hangup")
        bye, bye_source = refresh_server.wait_msg_from(10)
        if not bye.startswith("BYE %s SIP/2.0" % target):
            raise TestError("BYE did not use the refreshed Contact source")
        if with_route and "<sip:route.invalid:5099;lr>" not in bye:
            raise TestError("BYE Route header was not preserved")
        response = refresh_server.create_response(bye, 200, "OK")
        refresh_server.send_msg(response + "Content-Length: 0\r\n\r\n", bye_source)
        caller.expect(const.STATE_DISCONNECTED)
        caller.sync_stdout()
    finally:
        server.sock.close()
        final_server.sock.close()
        refresh_server.sock.close()


def check_incoming_contact(test):
    callee = test.process[1]
    server = sip.Dialog("127.0.0.1", callee.inst_param.sip_port)
    target = "sip:caller@unresolvable.invalid:5099"
    try:
        invite = server.create_invite(SDP)
        invite = invite.replace(sip.get_header(invite, "Contact"),
                                "<%s>" % target)
        server.send_msg(invite)
        for attempt in range(10):
            response, source = server.wait_msg_from(10)
            if sip.get_code(response) == 200:
                break
        else:
            raise TestError("Incoming call was not answered")
        server.send_msg(server.create_ack() + "\r\n", source)
        callee.expect(const.STATE_CONFIRMED)
        callee.send("call hangup")
        bye, source = server.wait_msg_from(10)
        if not bye.startswith("BYE %s SIP/2.0" % target):
            raise TestError("Incoming dialog BYE did not use the INVITE source")
        response = server.create_response(bye, 200, "OK")
        server.send_msg(response + "Content-Length: 0\r\n\r\n", source)
        callee.expect(const.STATE_DISCONNECTED)
        callee.sync_stdout()
    finally:
        server.sock.close()


def check_source_routing(test):
    check_prack(test)
    check_dialog(test, False)
    check_dialog(test, True)
    check_incoming_contact(test)


test_param = TestParam(
    "Contact and PRACK source routing is opt-in and preserves SIP headers",
    [InstanceParam("default", "--null-audio --no-tcp --use-100rel"),
     InstanceParam("contact", "--null-audio --no-tcp --use-srtp=0 "
                              "--auto-answer=200 --contact-use-src-addr")],
    func=check_source_routing)
