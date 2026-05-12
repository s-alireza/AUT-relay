# -*- coding: utf-8 -*-
import json
import base64
try:
    from urllib.parse import urlparse, parse_qs, unquote
except ImportError:
    from urlparse import urlparse, parse_qs
    from urllib import unquote

def parse_config_link(link):
    link = link.strip()
    if link.startswith("ss://"):
        return _parse_ss(link)
    elif link.startswith("vless://"):
        return _parse_vless(link)
    elif link.startswith("vmess://"):
        return _parse_vmess(link)
    else:
        return None

def _parse_ss(link):
    try:
        link = link.split("#")[0]
        body = link[5:]
        if "@" in body:
            user_part, server_part = body.rsplit("@", 1)
        else:
            decoded = base64.b64decode(body + "==").decode("utf-8")
            if "@" in decoded:
                user_part, server_part = decoded.rsplit("@", 1)
            else:
                return None
        try:
            user_part = base64.b64decode(user_part + "==").decode("utf-8")
        except Exception:
            pass
        method, password = user_part.split(":", 1)
        host, port = server_part.split(":", 1)
        return {
            "protocol": "shadowsocks",
            "settings": {
                "servers": [{
                    "address": host, "port": int(port),
                    "method": method, "password": password
                }]
            }
        }
    except Exception: return None

def _parse_vless(link):
    try:
        link = link.split("#")[0]
        body = link[8:]
        uuid_val, rest = body.split("@", 1)
        if "?" in rest:
            server_part, query = rest.split("?", 1)
        else:
            server_part, query = rest, ""
        host, port = server_part.rsplit(":", 1)
        params = parse_qs(query)
        get = lambda k, d="": params.get(k, [d])[0]
        outbound = {
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": host, "port": int(port),
                    "users": [{"id": uuid_val, "encryption": get("encryption", "none")}]
                }]
            },
            "streamSettings": {}
        }
        
        flow = get("flow", "")
        if flow:
            outbound["settings"]["vnext"][0]["users"][0]["flow"] = flow

        ss = outbound["streamSettings"]
        net = get("type", "tcp")
        ss["network"] = net
        security = get("security", "none")
        
        if security == "tls":
            ss["security"] = "tls"
            tls_settings = {"serverName": get("sni", host), "allowInsecure": True}
            fp = get("fp", "")
            if fp: tls_settings["fingerprint"] = fp
            alpn = get("alpn", "")
            if alpn: tls_settings["alpn"] = alpn.split(",")
            ss["tlsSettings"] = tls_settings
        elif security == "reality":
            ss["security"] = "reality"
            reality_settings = {
                "serverName": get("sni", host),
                "publicKey": get("pbk", ""),
                "shortId": get("sid", "")
            }
            fp = get("fp", "chrome")
            if fp: reality_settings["fingerprint"] = fp
            spx = get("spx", "")
            if spx: reality_settings["spiderX"] = spx
            ss["realitySettings"] = reality_settings

        if net == "ws":
            ss["wsSettings"] = {"path": unquote(get("path", "/"))}
            h = get("host", "")
            if h: ss["wsSettings"]["headers"] = {"Host": h}
        elif net == "grpc":
            ss["grpcSettings"] = {"serviceName": get("serviceName", ""), "multiMode": get("mode", "") == "multi"}
        return outbound
    except Exception: return None

def _parse_vmess(link):
    try:
        body = link[8:].split("#")[0]
        padding = 4 - len(body) % 4
        if padding != 4: body += "=" * padding
        data = json.loads(base64.b64decode(body).decode("utf-8"))
        outbound = {
            "protocol": "vmess",
            "settings": {
                "vnext": [{
                    "address": data.get("add", ""),
                    "port": int(data.get("port", 443)),
                    "users": [{
                        "id": data.get("id", ""),
                        "alterId": int(data.get("aid", 0)),
                        "security": data.get("scy", "auto")
                    }]
                }]
            },
            "streamSettings": {}
        }
        ss = outbound["streamSettings"]
        ss["network"] = data.get("net", "tcp")
        if data.get("tls") == "tls":
            ss["security"] = "tls"
            ss["tlsSettings"] = {"serverName": data.get("sni", data.get("add", "")), "allowInsecure": True}
        if ss["network"] == "ws":
            ss["wsSettings"] = {"path": data.get("path", "/"), "headers": {"Host": data.get("host", "")}}
        return outbound
    except Exception: return None
