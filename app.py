import streamlit as st
import json
import random
import os
import requests
import hashlib
import hmac
import base64
import time
import threading
import websocket
from datetime import datetime
from docx import Document
try:
    from streamlit_mic_recorder import mic_recorder
    MIC_AVAILABLE = True
except:
    MIC_AVAILABLE = False

# ── 加载环境变量 ──────────────────────────────────────────
def load_env():
    key = os.getenv("DEEPSEEK_API_KEY")
    if key:
        return key
    for enc in ['utf-8', 'gbk', 'utf-8-sig']:
        try:
            with open('.env', 'r', encoding=enc) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('DEEPSEEK_API_KEY='):
                        return line.split('=', 1)[1].strip()
        except:
            continue
    return None

DEEPSEEK_API_KEY = load_env()

def load_xunfei_configs():
    configs = []
    appid = os.getenv('XUNFEI_APPID', '')
    apikey = os.getenv('XUNFEI_APIKEY', '')
    apisecret = os.getenv('XUNFEI_APISECRET', '')
    if appid and apikey and apisecret:
        configs.append({'appid': appid, 'apikey': apikey, 'apisecret': apisecret})
        return configs
    for enc in ['utf-8', 'gbk', 'utf-8-sig']:
        try:
            with open('.env', 'r', encoding=enc) as f:
                lines = f.readlines()
            env = {}
            for line in lines:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
            for suffix in ['', '_1', '_2', '_3']:
                appid = env.get(f'XUNFEI_APPID{suffix}', '')
                apikey = env.get(f'XUNFEI_APIKEY{suffix}', '')
                apisecret = env.get(f'XUNFEI_APISECRET{suffix}', '')
                if appid and apikey and apisecret:
                    configs.append({'appid': appid, 'apikey': apikey, 'apisecret': apisecret})
            if configs:
                break
        except:
            continue
    return configs

XUNFEI_CONFIGS = load_xunfei_configs()

def get_xunfei_auth_url(host, path, apikey, apisecret):
    now = datetime.utcnow()
    date = now.strftime('%a, %d %b %Y %H:%M:%S GMT')
    signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    signature = base64.b64encode(
        hmac.new(apisecret.encode('utf-8'),
                 signature_origin.encode('utf-8'),
                 digestmod=hashlib.sha256).digest()
    ).decode('utf-8')
    auth_origin = f'api_key="{apikey}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
    auth = base64.b64encode(auth_origin.encode('utf-8')).decode('utf-8')
    from urllib.parse import quote
    return f"wss://{host}{path}?authorization={quote(auth)}&date={quote(date)}&host={host}"

def xunfei_speech_to_text(audio_bytes):
    if not XUNFEI_CONFIGS:
        return None, "未找到讯飞配置"
    config = XUNFEI_CONFIGS[0]
    appid = config['appid']
    apikey = config['apikey']
    apisecret = config['apisecret']
    host = "iat-api.xfyun.cn"
    path = "/v2/iat"
    url = get_xunfei_auth_url(host, path, apikey, apisecret)
    result_texts = []
    errors = []
    done = threading.Event()

    def on_message(ws, message):
        data = json.loads(message)
        code = data.get("code", -1)
        if code != 0:
            errors.append(f"讯飞错误: {code} - {data.get('message', '')}")
            done.set()
            return
        result = data.get("data", {}).get("result", {})
        words = result.get("ws", [])
        text = "".join([cw.get("w", "") for w in words for cw in w.get("cw", [])])
        if text:
            result_texts.append(text)
        status = data.get("data", {}).get("status", 0)
        if status == 2:
            done.set()

    def on_error(ws, error):
        errors.append(str(error))
        done.set()

    def on_close(ws, *args):
        done.set()

    def on_open(ws):
        def send_audio():
            time.sleep(0.3)
            try:
                start_frame = {
                    "common": {"app_id": appid},
                    "business": {"language": "en_us", "domain": "iat", "accent": "mandarin", "vad_eos": 5000},
                    "data": {"status": 0, "format": "audio/L16;rate=16000", "encoding": "raw",
                             "audio": base64.b64encode(audio_bytes[:1280]).decode()}
                }
                ws.send(json.dumps(start_frame))
                time.sleep(0.04)
                chunk_size = 1280
                offset = 1280
                while offset < len(audio_bytes):
                    chunk = audio_bytes[offset:offset+chunk_size]
                    frame = {"data": {"status": 1, "format": "audio/L16;rate=16000", "encoding": "raw",
                                      "audio": base64.b64encode(chunk).decode()}}
                    ws.send(json.dumps(frame))
                    offset += chunk_size
                    time.sleep(0.04)
                end_frame = {"data": {"status": 2, "format": "audio/L16;rate=16000", "encoding": "raw", "audio": ""}}
                ws.send(json.dumps(end_frame))
            except Exception as e:
                errors.append(f"发送错误: {e}")
                done.set()
        threading.Thread(target=send_audio).start()

    ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close, on_open=on_open)
    ws_thread = threading.Thread(target=ws.run_forever, kwargs={"sslopt": {"cert_reqs": 0}})
    ws_thread.daemon = True
    ws_thread.start()
    done.wait(timeout=30)
    try:
        ws.close()
    except:
        pass
    if errors:
        return None, errors[0]
    if result_texts:
        return "".join(result_texts), None
    return None, "未识别到语音内容，请重新录音"


def xunfei_pronunciation_score(audio_bytes, reference_text):
    if not XUNFEI_CONFIGS:
        return None, "未找到讯飞配置"
    config = XUNFEI_CONFIGS[0]
    appid = config['appid']
    apikey = config['apikey']
    apisecret = config['apisecret']
    host = "ise-api.xfyun.cn"
    path = "/v2/open-ise"
    url = get_xunfei_auth_url(host, path, apikey, apisecret)
    clean_text = reference_text.strip()
    for ch in ['(', ')', '[', ']', '{', '}', '@', '#', '$', '%', '&', '*']:
        clean_text = clean_text.replace(ch, '')
    formatted_text = "\uFEFF[content]\n" + clean_text
    result_score = [None]
    errors = []
    done = threading.Event()

    def on_message(ws, message):
        try:
            data = json.loads(message)
            code = data.get("code", -1)
            if code != 0:
                errors.append(f"评测错误码{code}: {data.get('message', '')}")
                done.set()
                return
            result_data = data.get("data", {})
            if result_data.get("status") == 2:
                raw = result_data.get("data", "")
                if raw:
                    try:
                        import xml.etree.ElementTree as ET
                        xml_str = base64.b64decode(raw).decode('utf-8', errors='ignore')
                        root = ET.fromstring(xml_str)
                        for tag in ["read_sentence", "read_chapter", "rec_paper"]:
                            elem = root.find(f".//{tag}")
                            if elem is not None:
                                total = elem.get("total_score", "0")
                                result_score[0] = float(total)
                                break
                    except Exception as e:
                        errors.append(f"XML解析失败: {e}")
                done.set()
        except Exception as e:
            errors.append(str(e))
            done.set()

    def on_error(ws, error):
        errors.append(str(error))
        done.set()

    def on_close(ws, *args):
        done.set()

    def on_open(ws):
        def send_data():
            time.sleep(0.3)
            try:
                ssb_frame = {
                    "common": {"app_id": appid},
                    "business": {"sub": "ise", "ent": "en_vip", "category": "read_sentence",
                                 "cmd": "ssb", "auf": "audio/L16;rate=16000", "aue": "raw",
                                 "tte": "utf-8", "ttp_skip": True, "rstcd": "utf8", "text": formatted_text},
                    "data": {"status": 0, "data": ""}
                }
                ws.send(json.dumps(ssb_frame))
                time.sleep(0.1)
                chunk_size = 1280
                total_len = len(audio_bytes)
                offset = 0
                is_first = True
                while offset < total_len:
                    chunk = audio_bytes[offset:offset + chunk_size]
                    offset += chunk_size
                    is_last = (offset >= total_len)
                    if is_first:
                        aus = 1; status = 1; is_first = False
                    elif is_last:
                        aus = 4; status = 2
                    else:
                        aus = 2; status = 1
                    frame = {"business": {"cmd": "auw", "aus": aus, "aue": "raw"},
                             "data": {"status": status, "data": base64.b64encode(chunk).decode(),
                                      "data_type": 1, "encoding": "raw"}}
                    ws.send(json.dumps(frame))
                    time.sleep(0.04)
            except Exception as e:
                errors.append(f"发送错误: {e}")
                done.set()
        threading.Thread(target=send_data, daemon=True).start()

    ise_ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close, on_open=on_open)
    ws_thread = threading.Thread(target=ise_ws.run_forever, kwargs={"sslopt": {"cert_reqs": 0}})
    ws_thread.daemon = True
    ws_thread.start()
    done.wait(timeout=30)
    try:
        ise_ws.close()
    except:
        pass
    if errors and not result_score[0]:
        return None, errors[0]
    if result_score[0] is not None:
        ielts_score = round_ielts_score(result_score[0] / 100 * 9)
        return ielts_score, None
    return None, "未获取到发音评分"


def convert_audio_to_pcm16k(audio_bytes):
    import io
    import subprocess
    import tempfile

    try:
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as tmp_in:
            tmp_in.write(audio_bytes)
            tmp_in_path = tmp_in.name
        tmp_out_path = tmp_in_path.replace('.webm', '.pcm')
        ffmpeg_candidates = ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', 'ffmpeg', r'D:\YoutubeDownloader.win-x64\ffmpeg.exe']
        ffmpeg_cmd = None
        for p in ffmpeg_candidates:
            try:
                r = subprocess.run([p, '-version'], capture_output=True, timeout=5)
                if r.returncode == 0:
                    ffmpeg_cmd = p
                    break
            except:
                continue
        if ffmpeg_cmd:
            result = subprocess.run([ffmpeg_cmd, '-y', '-i', tmp_in_path, '-ar', '16000', '-ac', '1', '-f', 's16le', tmp_out_path],
                                    capture_output=True, timeout=15)
            if result.returncode == 0 and os.path.exists(tmp_out_path):
                with open(tmp_out_path, 'rb') as f:
                    pcm_data = f.read()
                try:
                    os.unlink(tmp_in_path)
                    os.unlink(tmp_out_path)
                except:
                    pass
                if len(pcm_data) > 0:
                    return pcm_data
        try:
            os.unlink(tmp_in_path)
        except:
            pass
    except Exception:
        pass

    try:
        from pydub import AudioSegment
        import pydub.utils as pydub_utils
        for fp in ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', 'ffmpeg']:
            try:
                r = subprocess.run([fp, '-version'], capture_output=True, timeout=5)
                if r.returncode == 0:
                    pydub_utils.FFMPEG = fp
                    pydub_utils.FFPROBE = fp.replace('ffmpeg', 'ffprobe')
                    break
            except:
                continue
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        raw = audio.raw_data
        if len(raw) > 0:
            return raw
    except Exception:
        pass

    try:
        import wave
        import audioop
        with io.BytesIO(audio_bytes) as buf:
            with wave.open(buf, 'rb') as wav:
                channels = wav.getnchannels()
                sampwidth = wav.getsampwidth()
                framerate = wav.getframerate()
                frames = wav.readframes(wav.getnframes())
        if channels > 1:
            frames = audioop.tomono(frames, sampwidth, 0.5, 0.5)
        if sampwidth != 2:
            frames = audioop.lin2lin(frames, sampwidth, 2)
        if framerate != 16000:
            frames, _ = audioop.ratecv(frames, 2, 1, framerate, 16000, None)
        if len(frames) > 0:
            return frames
    except Exception:
        pass

    return audio_bytes


# ── 页面配置 ──────────────────────────────────────────────
st.set_page_config(page_title="Embo - 雅思备考助手", page_icon="📗", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background-color: #f7faf3;
}
.block-container { 
    padding: 1rem !important; max-width: 500px !important; margin: 1rem auto !important;
    background: white !important; border-radius: 18px !important;
    box-shadow: 0 4px 24px rgba(59,109,17,0.15), 0 1px 4px rgba(0,0,0,0.08) !important;
    border: 2px solid #C0DD97 !important;
}
.stApp { background: #deebc8 !important; }
#MainMenu, footer, header { visibility: hidden; }
.embo-header { background: #3B6D11; padding: 16px 20px; display: flex; align-items: center; justify-content: space-between; }
.embo-header-title { color: white; font-size: 18px; font-weight: 600; margin: 0; }
.embo-header-sub { color: #C0DD97; font-size: 12px; }
.welcome-hero { background: #f7faf3; text-align: center; padding: 40px 24px 20px; }
.welcome-title { font-size: 24px; font-weight: 600; color: #27500A; margin: 12px 0 4px; }
.welcome-en { font-size: 14px; color: #639922; margin-bottom: 8px; }
.welcome-tagline { font-size: 13px; color: #639922; background: white; border: 1px solid #C0DD97; border-radius: 99px; padding: 4px 16px; display: inline-block; margin-bottom: 16px; }
.greeting-box { background: white; border: 1px solid #C0DD97; border-radius: 12px; padding: 16px 20px; text-align: center; margin: 16px 0; }
.greeting-cn { font-size: 18px; font-weight: 600; color: #3B6D11; margin-bottom: 4px; }
.greeting-en { font-size: 13px; color: #639922; }
.feature-card { background: white; border: 1px solid #e0edd0; border-radius: 12px; padding: 16px; margin-bottom: 10px; display: flex; align-items: center; gap: 14px; }
.feature-icon { width: 44px; height: 44px; background: #EAF3DE; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 22px; flex-shrink: 0; }
.feature-title { font-size: 15px; font-weight: 600; color: #27500A; margin-bottom: 2px; }
.feature-desc { font-size: 12px; color: #88a870; }
.features-bar { display: flex; justify-content: space-around; padding: 20px 16px; border-top: 1px solid #e0edd0; background: white; margin-top: 16px; }
.feat-item { text-align: center; }
.feat-icon { font-size: 22px; margin-bottom: 4px; }
.feat-text { font-size: 11px; color: #639922; }
.score-big { text-align: center; padding: 20px; background: white; border-radius: 16px; border: 1px solid #e0edd0; margin-bottom: 16px; }
.score-number { font-size: 64px; font-weight: 700; color: #3B6D11; line-height: 1; }
.score-label { font-size: 13px; color: #88a870; margin-top: 4px; }
.dim-card { background: white; border-radius: 12px; padding: 14px 16px; margin-bottom: 10px; border: 1px solid #e0edd0; }
.dim-row { display: flex; justify-content: space-between; margin-bottom: 6px; }
.dim-name { font-size: 13px; color: #27500A; }
.dim-score { font-size: 13px; font-weight: 600; color: #3B6D11; }
.bar-bg { height: 6px; background: #e0edd0; border-radius: 99px; }
.bar-fill { height: 6px; background: #639922; border-radius: 99px; }
.feedback-good { background: #f0f8e8; border-left: 4px solid #639922; padding: 12px 14px; margin-bottom: 10px; border-radius: 0 8px 8px 0; }
.feedback-improve { background: #fff8ee; border-left: 4px solid #EF9F27; padding: 12px 14px; margin-bottom: 10px; border-radius: 0 8px 8px 0; }
.feedback-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.feedback-text { font-size: 13px; color: #555; line-height: 1.6; }
.question-card { background: #EAF3DE; border-left: 4px solid #3B6D11; border-radius: 0 12px 12px 0; padding: 16px; margin-bottom: 16px; font-size: 14px; color: #27500A; line-height: 1.7; }
.model-bar { background: white; border-top: 1px solid #e0edd0; padding: 10px 16px; font-size: 12px; color: #88a870; text-align: center; margin-top: 20px; }
.badge { display: inline-block; font-size: 11px; padding: 2px 10px; border-radius: 99px; margin-right: 6px; margin-bottom: 8px; }
.badge-green { background: #3B6D11; color: white; }
.badge-light { background: white; color: #3B6D11; border: 1px solid #639922; }
.stButton > button { background: #3B6D11 !important; color: white !important; border: none !important; border-radius: 10px !important; padding: 10px 20px !important; font-size: 15px !important; font-weight: 500 !important; width: 100% !important; }
.stButton > button:hover { background: #27500A !important; }
.stTextInput > div > div > input { border: 1px solid #C0DD97 !important; border-radius: 10px !important; padding: 10px 14px !important; background: white !important; }
.stTextArea > div > div > textarea { border: 1px solid #C0DD97 !important; border-radius: 10px !important; background: white !important; }
.stSelectbox > div > div { border: 1px solid #C0DD97 !important; border-radius: 10px !important; }
.stRadio > div { gap: 8px !important; }
</style>
""", unsafe_allow_html=True)


# ── 工具函数 ──────────────────────────────────────────────

def parse_part1_questions(doc):
    topics = []
    current_topic = None
    current_qas = []
    current_q = None
    current_a = None
    current_tips = []

    def save_qa():
        if current_q:
            current_qas.append({"question": current_q, "answer": current_a or "", "tips": " | ".join(current_tips)})

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        is_topic = (not text.startswith("Q:") and not text.startswith("A:") and
                    not text.startswith("参考") and not text.startswith("表达") and
                    not text.startswith("你") and not text.startswith("*") and
                    len(text) < 30 and any(c.isupper() for c in text[:5]))
        if is_topic:
            save_qa()
            current_q = None; current_a = None; current_tips = []
            if current_topic and current_qas:
                topics.append({"topic": current_topic, "questions": current_qas})
            current_topic = text.strip()
            current_qas = []
        elif text.startswith("Q:") or text.startswith("Q："):
            save_qa()
            current_q = text[2:].strip(); current_a = None; current_tips = []
        elif text.startswith("A:") or text.startswith("A："):
            current_a = text[2:].strip()
        elif text.startswith("参考思路") or text.startswith("表达亮点"):
            current_tips.append(text)

    save_qa()
    if current_topic and current_qas:
        topics.append({"topic": current_topic, "questions": current_qas})
    return topics


def parse_part23_questions(doc):
    items = []
    current_item = None
    in_taskcard = False
    in_part3 = False

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if "Task Card" in text or "task card" in text.lower():
            if current_item and (current_item.get("cue_card") or current_item.get("topic")):
                items.append(current_item)
            current_item = {"topic": "", "cue_card": [], "part3": []}
            in_taskcard = True; in_part3 = False
            continue
        if current_item is None:
            continue
        if "Part 3" in text or "延伸问答" in text or "Part3" in text:
            in_taskcard = False; in_part3 = True
            continue
        skip_prefixes = ["参考思路", "表达亮点", "A:", "A：", "中文题意", "中文问题", "参考答案", "高分参考"]
        if any(text.startswith(p) for p in skip_prefixes):
            continue
        if in_part3:
            if text.startswith("Q:") or text.startswith("Q："):
                q = text[2:].strip()
                if q:
                    current_item["part3"].append(q)
        elif in_taskcard:
            has_english = any(c.isascii() and c.isalpha() for c in text)
            if not has_english:
                continue
            if not current_item["topic"]:
                current_item["topic"] = text
            elif (text.startswith("You should") or text.startswith("•") or
                  text.startswith("-") or text.startswith("And ")):
                current_item["cue_card"].append(text)

    if current_item and (current_item.get("cue_card") or current_item.get("topic")):
        items.append(current_item)
    return [i for i in items if i.get("topic")]


def parse_task1_questions(doc):
    import base64
    items = []
    current = {"question": "", "essay_type": "", "sample": "", "image_b64": "", "image_idx": -1}
    in_answer = False
    img_counter = [0]

    def save_item():
        if current["question"]:
            items.append({"question": current["question"].strip(), "essay_type": current["essay_type"].strip(),
                          "sample": current["sample"].strip(), "image_b64": current["image_b64"], "image_idx": current["image_idx"]})

    def get_para_images(para):
        imgs = []
        for run in para.runs:
            for elem in run._element:
                if elem.tag.endswith("}drawing"):
                    for pic in elem.iter():
                        if pic.tag.endswith("}blip"):
                            rId = pic.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                            if rId:
                                try:
                                    img_part = doc.part.related_parts[rId]
                                    b64 = base64.b64encode(img_part.blob).decode()
                                    ct = img_part.content_type
                                    imgs.append(f"data:{ct};base64,{b64}")
                                except:
                                    pass
        return imgs

    for para in doc.paragraphs:
        text = para.text.strip()
        para_imgs = get_para_images(para)
        if para_imgs and not in_answer:
            current["image_b64"] = para_imgs[0]
            current["image_idx"] = img_counter[0]
            img_counter[0] += 1
            continue
        if not text:
            continue
        if text.startswith("────") or text.startswith("---"):
            save_item()
            current = {"question": "", "essay_type": "", "sample": "", "image_b64": "", "image_idx": -1}
            in_answer = False
            continue
        if text.startswith("题型："):
            current["essay_type"] = text[3:].strip()
            continue
        if text.startswith("Q:") or text.startswith("Q："):
            current["question"] = text[2:].strip()
            in_answer = False
            continue
        if text == "图表：" or text.startswith("原始资料中"):
            continue
        if text.startswith("A:") or text.startswith("A："):
            current["sample"] = text[2:].strip()
            in_answer = True
            continue
        if text.startswith("参考思路") or text.startswith("表达亮点"):
            in_answer = False
            continue
        if in_answer and current["sample"]:
            has_english = any(c.isascii() and c.isalpha() for c in text)
            if has_english or len(text) > 20:
                current["sample"] += "\n" + text

    save_item()
    return [i for i in items if i["question"]]


def parse_task2_questions(doc):
    items = []
    current = {"question": "", "sample": ""}
    in_answer = False

    def save_item():
        if current["question"].strip():
            sample = current["sample"].strip()
            clean_lines = []
            for line in sample.split("\n"):
                line = line.strip()
                if not line:
                    continue
                ascii_ratio = sum(1 for c in line if c.isascii()) / max(len(line), 1)
                if ascii_ratio > 0.5 or len(line) < 5:
                    clean_lines.append(line)
            items.append({"question": current["question"].strip(), "sample": "\n".join(clean_lines)})

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if text.startswith("────") or text.startswith("---") or set(text) <= {"-", "─", " "}:
            save_item()
            current = {"question": "", "sample": ""}
            in_answer = False
            continue
        if text[0].isdigit() and "." in text[:4] and "Q:" not in text:
            continue
        if text.startswith("题型：") or text.startswith("题型:"):
            continue
        if text.startswith("Q:") or text.startswith("Q："):
            current["question"] = text[2:].strip()
            in_answer = False
            continue
        if text.startswith("A:") or text.startswith("A："):
            current["sample"] = text[2:].strip()
            in_answer = True
            continue
        if text.startswith("参考思路") or text.startswith("表达亮点"):
            in_answer = False
            continue
        if in_answer:
            ascii_ratio = sum(1 for c in text if c.isascii()) / max(len(text), 1)
            if ascii_ratio > 0.4:
                current["sample"] += "\n" + text if current["sample"] else text

    save_item()
    return [i for i in items if i["question"]]


def load_question_bank():
    data_dir = "data"
    bank = {}
    if not os.path.exists(data_dir):
        return bank
    for filename in os.listdir(data_dir):
        if not filename.endswith(".docx"):
            continue
        path = os.path.join(data_dir, filename)
        fname = filename.lower()
        try:
            doc = Document(path)
        except:
            continue
        if "part1" in fname or "part 1" in fname:
            topics = parse_part1_questions(doc)
            if "当季" in filename or "current" in fname:
                bank["speaking_p1_current"] = topics
            else:
                bank["speaking_p1_past"] = topics
        elif "part2" in fname or "part2&3" in fname:
            items = parse_part23_questions(doc)
            if "当季" in filename or "current" in fname:
                bank["speaking_p23_current"] = items
            else:
                bank["speaking_p23_past"] = items
        elif "task1" in fname or "task 1" in fname:
            bank["writing_task1"] = parse_task1_questions(doc)
        elif "task2" in fname or "task 2" in fname:
            bank["writing_task2"] = parse_task2_questions(doc)
        elif "mock" in fname or "模拟" in filename:
            text = "\n".join([p.text.strip() for p in doc.paragraphs if p.text.strip()])
            bank["writing_mock"] = [q.strip() for q in text.split("---") if q.strip()]
    return bank


def call_deepseek(system_prompt, user_prompt):
    if not DEEPSEEK_API_KEY:
        return "❌ 未找到DeepSeek API Key，请检查.env文件"
    try:
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat",
                  "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                  "max_tokens": 2000, "temperature": 0.7},
            timeout=30
        )
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"❌ API调用失败：{str(e)}"


def round_ielts_score(score):
    try:
        score = float(score)
        score = max(0, min(9, score))
        return round(score * 2) / 2
    except:
        return 0.0

def calc_ielts_overall(scores):
    try:
        avg = sum(scores) / len(scores)
        frac = avg - int(avg)
        if frac < 0.25:
            overall = float(int(avg))
        elif frac < 0.75:
            overall = float(int(avg)) + 0.5
        else:
            overall = float(int(avg)) + 1.0
        return min(9.0, overall)
    except:
        return 0.0

def parse_json_result(text):
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break
    try:
        return json.loads(text)
    except:
        import re
        match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass
    return None


# ── 官方评分标准 ──────────────────────────────────────────

WRITING_DESCRIPTOR_T1 = """
【Task Achievement评分标准】
9: fully satisfies all requirements; clearly presents fully developed response
8: covers all requirements sufficiently; presents highlights and illustrates key features clearly and appropriately
7: covers requirements; presents clear overview; clearly presents and highlights key features but could be more fully extended
6: addresses requirements; presents overview with appropriate information; presents and highlights key features but details may be irrelevant or inaccurate
5: addresses task only partially; presents information with some organisation but lacks overall progression
4: responds minimally; format may be inappropriate; key features confused with detail

【Coherence and Cohesion评分标准】
9: uses cohesion so naturally it attracts no attention; skilfully manages paragraphing
8: sequences information logically; manages all aspects of cohesion well; uses paragraphing sufficiently
7: logically organises information; clear progression; uses range of cohesive devices appropriately though may be some under/over-use
6: arranges information coherently; clear overall progression; uses cohesive devices effectively but cohesion within sentences may be faulty or mechanical
5: presents information with some organisation but lacks overall progression; over-uses or inaccurately uses cohesive devices

【Lexical Resource评分标准】
9: uses wide range with very natural and sophisticated control; rare minor errors only as 'slips'
8: uses wide range fluently and flexibly to convey precise meanings; skilfully uses uncommon items; occasional inaccuracies in word choice
7: uses sufficient range allowing flexibility; uses less common items with some awareness of style; may produce occasional errors in word choice/spelling
6: uses adequate range; attempts less common vocabulary but with some inaccuracy; makes some errors in spelling/word formation that do not impede communication
5: uses limited range minimally adequate for task; noticeable errors in spelling/word formation that may cause difficulty

【Grammatical Range and Accuracy评分标准】
9: uses wide range naturally and appropriately; consistently accurate apart from native speaker slips
8: uses wide range flexibly; majority of sentences error-free; only occasional inappropriacies
7: uses variety of complex structures; produces frequent error-free sentences; has good grammar control but may make few errors
6: uses mix of simple and complex sentence forms; makes some errors in grammar/punctuation but rarely reduce communication
5: uses only limited range; attempts complex sentences but less accurate; frequent grammatical errors may cause difficulty
"""

WRITING_DESCRIPTOR_T2 = """
【Task Response评分标准】
9: fully addresses all parts; presents fully developed position with relevant, extended, well-supported ideas
8: sufficiently addresses all parts; presents well-developed response; relevant, extended and supported ideas
7: addresses all parts; presents clear position throughout; presents, extends and supports main ideas though may overgeneralise
6: addresses all parts though some more fully covered; presents relevant position; presents relevant main ideas but some inadequately developed
5: addresses task only partially; expresses position but development not always clear; presents some main ideas but limited and not sufficiently developed
4: responds minimally; position unclear; main ideas difficult to identify, irrelevant or not well supported

【Coherence and Cohesion评分标准】
9: uses cohesion so naturally it attracts no attention; skilfully manages paragraphing
8: sequences information logically; manages all aspects of cohesion well; uses paragraphing sufficiently and appropriately
7: logically organises information; clear progression; uses range of cohesive devices; presents clear central topic in each paragraph
6: arranges information coherently; clear overall progression; uses cohesive devices effectively but may be faulty or mechanical between sentences
5: presents information with some organisation but lacks overall progression; may over-use connectives; inadequate/inaccurate use of cohesive devices

【Lexical Resource评分标准】
9: uses wide range with very natural and sophisticated control; rare minor errors only as 'slips'
8: uses wide range fluently and flexibly; skilfully uses uncommon items; occasional inaccuracies in word choice
7: uses sufficient range allowing flexibility and precision; uses less common items with awareness of style; occasional errors in word choice/spelling
6: uses adequate range; attempts less common vocabulary but with inaccuracy; makes errors in spelling/word formation but do not impede communication
5: uses limited range minimally adequate; noticeable errors in spelling/word formation that cause difficulty for reader

【Grammatical Range and Accuracy评分标准】
9: uses wide range naturally; consistently accurate apart from native speaker slips
8: uses wide range flexibly; majority error-free; only occasional inappropriacies
7: uses variety of complex structures; frequent error-free sentences; good grammar control but few errors persist
6: uses mix of simple and complex forms; makes some errors but rarely reduce communication
5: uses only limited range; attempts complex sentences but less accurate than simple ones; frequent errors may cause difficulty
"""

SPEAKING_DESCRIPTOR = """
【Fluency and Coherence评分标准】
9: speaks fluently with only rare repetition or self-correction; any hesitation is content-related not language-related; develops topics fully and appropriately
8: speaks fluently with only occasional repetition or self-correction; hesitation is usually content-related; develops topics coherently and appropriately
7: speaks at length without noticeable effort or loss of coherence; may demonstrate language-related hesitation at times; uses range of connectives and discourse markers with some flexibility
6: willing to speak at length though may lose coherence at times due to occasional repetition or self-correction; uses range of discourse markers but not always appropriately
5: usually maintains flow but uses repetition/self-correction and/or slow speech; may over-use connectives; simple speech is fluent but more complex communication causes fluency problems
4: cannot respond without noticeable pauses; may speak slowly with frequent repetition; links basic sentences but with repetitious use of connectives

【Lexical Resource评分标准】
9: uses vocabulary with full flexibility and precision in all topics; uses idiomatic language naturally and accurately
8: uses wide vocabulary resource readily and flexibly to convey precise meaning; uses less common and idiomatic vocabulary skilfully; paraphrases effectively as required
7: uses vocabulary resource flexibly to discuss a variety of topics; uses some less common and idiomatic vocabulary and shows some awareness of style and collocation; uses paraphrase effectively
6: has wide enough vocabulary to discuss topics at length and make meaning clear in spite of inappropriacies; generally paraphrases successfully
5: manages to talk about familiar and unfamiliar topics but uses vocabulary with limited flexibility; attempts paraphrase but with mixed success
4: able to talk about familiar topics but only convey basic meaning on unfamiliar ones; makes frequent errors in word choice; rarely attempts paraphrase

【Grammatical Range and Accuracy评分标准】
9: uses full range of structures naturally and appropriately; consistently accurate apart from 'slips' characteristic of native speaker speech
8: uses wide range of structures flexibly; produces majority of error-free sentences; only very occasional inappropriacies or basic/non-systematic errors
7: uses range of complex structures with some flexibility; frequently produces error-free sentences; some grammatical mistakes persist
6: uses mix of simple and complex structures but with limited flexibility; may make frequent mistakes with complex structures; errors rarely cause comprehension problems
5: produces basic sentence forms with reasonable accuracy; uses limited range of more complex structures; errors in complex structures usually cause comprehension problems
4: produces basic sentence forms and some correct simple sentences; subordinate structures rare; errors frequent and may lead to misunderstanding
"""


def score_writing(essay, task_type, question=""):
    descriptor = WRITING_DESCRIPTOR_T1 if task_type == "Task 1" else WRITING_DESCRIPTOR_T2
    dim_name = "task" if task_type == "Task 1" else "task"

    system = f"""你是严格的雅思官方考官，必须严格按照以下官方Band Descriptor对作文进行评分。

{descriptor}

【评分规则】
- 每个维度分数只能是整数或0.5（如5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0等）
- 不能出现6.8、7.3等非法分数
- 评分必须严格对照上方标准，不能随意给高分，必须找到最符合描述的Band
- strengths必须引用作文中的具体句子或内容，说明哪里好、符合哪个Band的描述
- improvements必须具体指出作文哪里不足、对应哪个评分维度的哪条标准、应该如何改进
- 绝对不能给出空洞的评价，必须结合实际作文内容给出针对性反馈

只输出JSON，不要任何其他文字：
{{"task":6.0,"coherence":6.0,"lexical":6.0,"grammar":6.0,"strengths":"具体优点（引用作文内容）","improvements":"具体改进建议（结合评分标准）"}}"""

    user = f"题型：{task_type}\n"
    if question:
        user += f"题目：{question}\n"
    user += f"作文：\n{essay}"

    result = call_deepseek(system, user)
    data = parse_json_result(result)
    if data:
        for k in ["task", "coherence", "lexical", "grammar"]:
            if k in data:
                data[k] = round_ielts_score(data[k])
        scores = [data.get(k, 0) for k in ["task", "coherence", "lexical", "grammar"]]
        data["overall"] = calc_ielts_overall(scores)
    return data


def score_speaking_p1(question, answer):
    system = f"""你是严格的雅思官方口语考官，必须严格按照以下官方Band Descriptor对口语回答评分。

{SPEAKING_DESCRIPTOR}

【评分规则】
- 每个维度分数只能是整数或0.5（如5.0, 5.5, 6.0, 6.5, 7.0等）
- 不能出现6.8、7.3等非法分数
- 评分必须严格对照上方标准，找到最符合描述的Band，不能随意给高分
- strengths必须引用考生回答中的具体内容，说明哪里符合哪个Band的描述
- improvements必须具体指出回答哪里不足、对应哪个评分维度的哪条标准、如何改进
- 绝对不能给出空洞评价，必须结合实际回答内容

只输出JSON，不要任何其他文字：
{{"fluency":6.0,"lexical":6.0,"grammar":6.0,"strengths":"具体优点（引用回答内容）","improvements":"具体改进建议（结合评分标准）"}}"""

    user = f"题目：{question}\n\n考生回答：{answer}"
    result = call_deepseek(system, user)
    data = parse_json_result(result)
    if data:
        for k in ["fluency", "lexical", "grammar"]:
            if k in data:
                data[k] = round_ielts_score(data[k])
        scores = [data.get(k, 0) for k in ["fluency", "lexical", "grammar"]]
        data["overall"] = calc_ielts_overall(scores)
    return data


def score_speaking_overall(all_answers_text):
    system = f"""你是严格的雅思官方口语考官，必须严格按照以下官方Band Descriptor对考生的完整口语表现综合评分。

{SPEAKING_DESCRIPTOR}

【评分规则】
- 每个维度分数只能是整数或0.5（如5.0, 5.5, 6.0, 6.5, 7.0等）
- 不能出现6.8、7.3等非法分数
- 需综合考虑考生所有回答的整体表现，找到最符合描述的Band
- strengths必须引用考生回答中的具体内容，说明哪里符合哪个Band的描述
- improvements必须具体指出哪里不足、对应哪个评分维度的哪条标准、如何改进
- 绝对不能给出空洞评价

只输出JSON，不要任何其他文字：
{{"fluency":6.0,"lexical":6.0,"grammar":6.0,"strengths":"具体优点（引用回答内容）","improvements":"具体改进建议（结合评分标准）"}}"""

    result = call_deepseek(system, all_answers_text)
    data = parse_json_result(result)
    if data:
        for k in ["fluency", "lexical", "grammar"]:
            if k in data:
                data[k] = round_ielts_score(data[k])
        scores = [data.get(k, 0) for k in ["fluency", "lexical", "grammar"] if data.get(k, 0) > 0]
        if scores:
            data["overall"] = calc_ielts_overall(scores)
    return data


def generate_ai_question(task_type):
    system = "你是雅思出题专家，只输出题目内容，不要任何其他内容。"
    user = f"生成一道雅思{task_type}写作题，难度适中，贴近真实考题。"
    return call_deepseek(system, user)


# ── Session State ─────────────────────────────────────────
for k, v in {
    "page": "welcome", "user_name": "", "question_bank": None,
    "current_question": "", "current_source": "", "score_result": None,
    "ai_model": "DeepSeek（推荐）", "free_chat_history": [],
    "current_answer": "", "current_tips": "", "current_topic": "",
    "current_essay_type": "", "current_sample": "", "current_image_b64": "",
    "pronunciation_score": None,
    "current_p23_item": {}, "part2_answer": "", "part3_transition": "",
    "part3_index": 0, "part3_answers": [], "part3_followups": [],
    "exam_phase": "setup", "exam_part1_questions": [],
    "exam_part1_index": 0, "exam_part1_answers": [], "exam_difficulty": "标准"
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.question_bank is None:
    st.session_state.question_bank = load_question_bank()


# ── 欢迎页 ────────────────────────────────────────────────
def page_welcome():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if os.path.exists("embo.png"):
            st.image("embo.png", width=120)
    st.markdown("""
    <div class="welcome-hero">
        <div class="welcome-title">你好，欢迎来到 Embo！</div>
        <div class="welcome-en">Hello, welcome to Embo!</div>
        <div class="welcome-tagline">✦ 你的雅思AI备考伙伴</div>
    </div>
    """, unsafe_allow_html=True)
    name = st.text_input("名字", placeholder="请输入你的名字 / Enter your name", label_visibility="collapsed")
    if name:
        st.markdown(f"""
        <div class="greeting-box">
            <div class="greeting-cn">你好，{name}！我是 Embo 👋</div>
            <div class="greeting-en">Hello, {name}! I'm Embo, your IELTS companion.</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("🚀 开始使用 · Get Started"):
            st.session_state.user_name = name
            st.session_state.page = "home"
            st.rerun()
    st.markdown("""
    <div class="features-bar">
        <div class="feat-item"><div class="feat-icon">🤖</div><div class="feat-text">AI智能陪伴</div></div>
        <div class="feat-item"><div class="feat-icon">🎯</div><div class="feat-text">精准提分</div></div>
        <div class="feat-item"><div class="feat-icon">📈</div><div class="feat-text">个性化学习</div></div>
        <div class="feat-item"><div class="feat-icon">⭐</div><div class="feat-text">有趣高效</div></div>
    </div>
    <div style="text-align:center;padding:12px;font-size:12px;color:#C0DD97;">✦ 学习雅思，Embo 一路相伴 ✦</div>
    """, unsafe_allow_html=True)


# ── 主页面 ────────────────────────────────────────────────
def page_home():
    st.markdown(f"""
    <div class="embo-header">
        <div><div class="embo-header-title">Embo</div><div class="embo-header-sub">你好，{st.session_state.user_name} 👋</div></div>
        <div style="font-size:28px;">📗</div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    tab1, tab2 = st.tabs(["✍️ 写作练习", "🎤 口语练习"])
    with tab1:
        st.markdown("""<div class="feature-card"><div class="feature-icon">📝</div><div><div class="feature-title">直接评分</div><div class="feature-desc">粘贴作文，AI按四维度打分并给出建议</div></div></div>""", unsafe_allow_html=True)
        if st.button("进入直接评分 →", key="btn_direct"):
            st.session_state.page = "writing_direct"; st.rerun()
        st.markdown("""<div class="feature-card"><div class="feature-icon">📋</div><div><div class="feature-title">出题练习</div><div class="feature-desc">从题库抽题或AI出题，限时或自由作答</div></div></div>""", unsafe_allow_html=True)
        if st.button("进入出题练习 →", key="btn_practice"):
            st.session_state.page = "writing_practice"; st.rerun()
    with tab2:
        st.markdown("""<div class="feature-card"><div class="feature-icon">💬</div><div><div class="feature-title">口语陪练</div><div class="feature-desc">选择Part，随机出题，录音作答后AI评分</div></div></div>""", unsafe_allow_html=True)
        if st.button("进入口语陪练 →", key="btn_speaking"):
            st.session_state.page = "speaking_practice"; st.rerun()
        st.markdown("""<div class="feature-card"><div class="feature-icon">🏆</div><div><div class="feature-title">模拟考试</div><div class="feature-desc">按真实流程考试，AI追问，综合打分</div></div></div>""", unsafe_allow_html=True)
        if st.button("进入模拟考试 →", key="btn_exam"):
            st.session_state.page = "speaking_exam"; st.rerun()
    st.markdown("<br>", unsafe_allow_html=True)
    model = st.selectbox("🤖 文字评分模型", ["DeepSeek（推荐）", "讯飞星火", "Claude（限额）"], key="model_select")
    st.session_state.ai_model = model
    st.markdown('<div class="model-bar">语音识别由讯飞提供，文字评分模型可选</div>', unsafe_allow_html=True)


# ── 写作直接评分 ──────────────────────────────────────────
def page_writing_direct():
    st.markdown('<div class="embo-header"><div class="embo-header-title">✍️ 直接评分</div></div>', unsafe_allow_html=True)
    if st.button("← 返回主页", key="back_direct"):
        st.session_state.page = "home"; st.rerun()
    task_type = st.radio("选择题型", ["Task 1", "Task 2"], horizontal=True)
    question = st.text_area("题目（可选）", height=80, placeholder="粘贴题目内容，有题目评分更准确；也可以留空...")
    if task_type == "Task 1":
        st.caption("📊 Task 1含图表，可上传图表图片供参考")
        uploaded_img = st.file_uploader("上传图表图片（可选）", type=["png", "jpg", "jpeg"], key="chart_img")
        if uploaded_img:
            st.image(uploaded_img, caption="已上传图表", use_container_width=True)
    essay = st.text_area("粘贴或输入你的作文", height=220, placeholder="在此输入雅思作文...")
    word_count = len(essay.split()) if essay.strip() else 0
    min_words = 150 if task_type == "Task 1" else 250
    st.caption(f"已写 {word_count} 词 | 最少要求 {min_words} 词")
    if st.button("📊 提交评分", key="submit_direct"):
        if not essay.strip():
            st.warning("请先输入作文内容！")
        elif word_count < min_words:
            st.warning(f"作文太短！要求至少 {min_words} 词，当前 {word_count} 词")
        else:
            with st.spinner("Embo 正在评分中..."):
                result = score_writing(essay, task_type, question=question)
            if result:
                st.session_state.score_result = result
                st.session_state.current_source = "direct"
                st.session_state.page = "writing_result"; st.rerun()
            else:
                st.error("评分失败，请稍后重试")


# ── 写作出题练习 ──────────────────────────────────────────
def page_writing_practice():
    st.markdown('<div class="embo-header"><div class="embo-header-title">📋 出题练习</div></div>', unsafe_allow_html=True)
    if st.button("← 返回主页", key="back_practice"):
        st.session_state.page = "home"; st.rerun()
    task_type = st.radio("选择题型", ["Task 1", "Task 2"], horizontal=True)
    if task_type == "Task 1":
        if st.button("🎲 随机出题", key="get_question"):
            bank = st.session_state.question_bank
            questions = bank.get("writing_task1", [])
            if questions:
                item = random.choice(questions)
                st.session_state.current_question = item["question"]
                st.session_state.current_essay_type = item.get("essay_type", "")
                st.session_state.current_sample = item.get("sample", "")
                st.session_state.current_image_b64 = item.get("image_b64", "")
                st.session_state.current_source = "真题库"
                st.session_state.page = "writing_answer"; st.rerun()
            else:
                st.warning("Task 1题库暂无内容！")
    else:
        source = st.radio("选择题目来源", ["题库", "AI出题"], horizontal=True)
        if st.button("🎲 随机出题", key="get_question"):
            bank = st.session_state.question_bank
            if source == "题库":
                questions = bank.get("writing_task2", [])
                if questions:
                    chosen = random.choice(questions)
                    if isinstance(chosen, dict):
                        st.session_state.current_question = chosen.get("question", "")
                        st.session_state.current_sample = chosen.get("sample", "")
                    else:
                        st.session_state.current_question = chosen
                        st.session_state.current_sample = ""
                    st.session_state.current_image_b64 = ""
                    st.session_state.current_source = "题库"
                else:
                    with st.spinner("题库为空，AI正在生成题目..."):
                        st.session_state.current_question = generate_ai_question("Task 2")
                    st.session_state.current_source = "AI出题"
            else:
                with st.spinner("AI正在出题..."):
                    st.session_state.current_question = generate_ai_question("Task 2")
                st.session_state.current_sample = ""
                st.session_state.current_image_b64 = ""
                st.session_state.current_source = "AI出题"
            st.session_state.current_essay_type = ""
            st.session_state.page = "writing_answer"; st.rerun()


# ── 写作答题 ──────────────────────────────────────────────
def page_writing_answer():
    st.markdown('<div class="embo-header"><div class="embo-header-title">✍️ 答题</div></div>', unsafe_allow_html=True)
    if st.button("← 重新选题", key="back_answer"):
        st.session_state.page = "writing_practice"; st.rerun()
    source = st.session_state.current_source
    essay_type = st.session_state.get("current_essay_type", "")
    task_label = "Task 1" if essay_type else "Task 2"
    type_badge = f'<span class="badge badge-light">{essay_type}</span>' if essay_type else ""
    st.markdown(f"""
    <div class="question-card">
        <span class="badge badge-green">{task_label}</span>
        <span class="badge badge-light">{source}</span>
        {type_badge}<br><br>
        {st.session_state.current_question}
    </div>
    """, unsafe_allow_html=True)
    image_b64 = st.session_state.get("current_image_b64", "")
    if image_b64 and source == "真题库" and essay_type:
        st.markdown("**📊 题目图表：**")
        st.markdown(f'<img src="{image_b64}" style="max-width:100%;border-radius:8px;margin-bottom:12px">', unsafe_allow_html=True)
    min_words = 150 if essay_type else 250
    target = min_words
    essay = st.text_area("在此输入你的作文", height=280, placeholder="开始写作...")
    word_count = len(essay.split()) if essay.strip() else 0
    st.progress(min(word_count / target, 1.0), text=f"已写 {word_count} 词 | 目标 {target} 词")
    if st.button("📊 提交评分 →", key="submit_answer"):
        if not essay.strip() or word_count < 30:
            st.warning("请先写一些内容再提交！")
        else:
            task_type_submit = "Task 1" if min_words == 150 else "Task 2"
            with st.spinner("Embo 正在评分中..."):
                result = score_writing(essay, task_type_submit, question=st.session_state.current_question)
            if result:
                st.session_state.score_result = result
                st.session_state.page = "writing_result"; st.rerun()
            else:
                st.error("评分失败，请稍后重试")


# ── 写作评分结果 ──────────────────────────────────────────
def page_writing_result():
    st.markdown('<div class="embo-header"><div class="embo-header-title">📊 评分结果</div></div>', unsafe_allow_html=True)
    result = st.session_state.score_result
    if not result:
        st.error("没有评分结果，请返回重试")
        if st.button("返回"): st.session_state.page = "home"; st.rerun()
        return
    overall = result.get("overall", 0)
    st.markdown(f"""
    <div class="score-big">
        <div class="score-number">{overall}</div>
        <div class="score-label">综合评分（满分 9.0）</div>
    </div>
    """, unsafe_allow_html=True)
    for name, key in [("Task Achievement","task"),("Coherence & Cohesion","coherence"),("Lexical Resource","lexical"),("Grammatical Range","grammar")]:
        score = result.get(key, 0)
        pct = int(score / 9 * 100)
        st.markdown(f"""
        <div class="dim-card">
            <div class="dim-row"><span class="dim-name">{name}</span><span class="dim-score">{score}</span></div>
            <div class="bar-bg"><div class="bar-fill" style="width:{pct}%"></div></div>
        </div>
        """, unsafe_allow_html=True)
    st.markdown(f"""
    <div class="feedback-good">
        <div class="feedback-title" style="color:#3B6D11">✅ 优点</div>
        <div class="feedback-text">{result.get('strengths','')}</div>
    </div>
    <div class="feedback-improve">
        <div class="feedback-title" style="color:#854F0B">💡 改进建议</div>
        <div class="feedback-text">{result.get('improvements','')}</div>
    </div>
    """, unsafe_allow_html=True)
    sample = st.session_state.get("current_sample", "")
    if sample and st.session_state.current_source in ["真题库", "题库", "模拟题库"]:
        with st.expander("📖 查看高分范文"):
            clean_lines = []
            for line in sample.split("\n"):
                line = line.strip()
                if not line:
                    clean_lines.append("")
                    continue
                ascii_ratio = sum(1 for c in line if c.isascii()) / max(len(line), 1)
                if ascii_ratio > 0.4:
                    clean_lines.append(line)
            clean_sample = "\n".join(clean_lines).strip()
            st.markdown(f'<div style="font-size:14px;line-height:1.8;color:#333;padding:8px">{clean_sample.replace(chr(10), "<br>")}</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 再练一篇", key="retry"):
            st.session_state.page = "writing_practice"; st.rerun()
    with col2:
        if st.button("🏠 返回主页", key="home_result"):
            st.session_state.page = "home"; st.rerun()


# ── 口语陪练 ──────────────────────────────────────────────
def page_speaking_practice():
    st.markdown('<div class="embo-header"><div class="embo-header-title">🎤 口语陪练</div></div>', unsafe_allow_html=True)
    if st.button("← 返回主页", key="back_speaking"):
        st.session_state.page = "home"; st.rerun()
    mode = st.radio("选择练习方式", ["随机出题作答", "自由对话陪练"], horizontal=True)
    if mode == "随机出题作答":
        part = st.radio("选择练习 Part", ["Part 1", "Part 2 & 3"], horizontal=True)
        season = st.radio("选择题目季节", ["当季真题", "往季真题"], horizontal=True)
        bank = st.session_state.question_bank
        if st.button("🎲 随机出题", key="get_speaking"):
            if part == "Part 1":
                key = "speaking_p1_current" if season == "当季真题" else "speaking_p1_past"
                topics = bank.get(key, [])
                if topics:
                    topic = random.choice(topics)
                    questions = topic.get("questions", [])
                    if questions:
                        qa = random.choice(questions)
                        st.session_state.current_question = qa.get("question", "")
                        st.session_state.current_answer = qa.get("answer", "")
                        st.session_state.current_tips = qa.get("tips", "")
                        st.session_state.current_topic = topic.get("topic", "")
                    else:
                        st.warning("题库格式有误！"); return
                else:
                    st.warning("题库暂无内容！"); return
            else:
                key = "speaking_p23_current" if season == "当季真题" else "speaking_p23_past"
                items = bank.get(key, [])
                if items:
                    item = random.choice(items)
                    st.session_state.current_p23_item = item
                    st.session_state.current_topic = item.get("topic", "")
                    st.session_state.current_answer = ""
                    st.session_state.current_tips = ""
                else:
                    st.warning("题库暂无内容！"); return
                st.session_state.page = "speaking_part2"; st.rerun()
                return
            st.session_state.page = "speaking_record"; st.rerun()
    else:
        st.markdown('<div class="question-card">🎙️ 自由对话模式：AI扮演雅思考官，与你进行真实对话练习。</div>', unsafe_allow_html=True)
        if st.button("💬 开始自由对话", key="start_free_chat"):
            st.session_state.free_chat_history = []
            st.session_state.page = "speaking_free_chat"; st.rerun()


# ── 口语录音作答（Part1） ────────────────────────────────
def page_speaking_record():
    st.markdown('<div class="embo-header"><div class="embo-header-title">🎤 录音作答</div></div>', unsafe_allow_html=True)
    if st.button("← 重新选题", key="back_record"):
        for k in ["recognized_text_p1", "answer_text_p1", "last_audio_id_p1", "last_pcm_p1", "pron_done_p1"]:
            st.session_state[k] = "" if "text" in k else None if "id" in k or "pcm" in k else False
        st.session_state.pronunciation_score = None
        st.session_state.page = "speaking_practice"; st.rerun()

    topic = st.session_state.get("current_topic", "")
    question = st.session_state.get("current_question", "")
    ref_answer = st.session_state.get("current_answer", "")
    tips = st.session_state.get("current_tips", "")
    if topic:
        st.markdown(f"<div style='font-size:12px;color:#639922;margin-bottom:6px'>话题：{topic}</div>", unsafe_allow_html=True)
    st.markdown(f'<div class="question-card"><b>Q: {question}</b></div>', unsafe_allow_html=True)

    for k, default in [("recognized_text_p1", ""), ("last_audio_id_p1", None), ("last_pcm_p1", None), ("pron_done_p1", False)]:
        if k not in st.session_state:
            st.session_state[k] = default

    if MIC_AVAILABLE and XUNFEI_CONFIGS:
        st.markdown("**🎙️ 点击录音按钮开始作答：**")
        audio = mic_recorder(start_prompt="🔴 开始录音", stop_prompt="⏹️ 停止录音", just_once=True, key="recorder_part1")
        if audio and audio.get("bytes"):
            audio_id = len(audio["bytes"])
            if st.session_state.get("last_audio_id_p1") != audio_id:
                st.session_state.last_audio_id_p1 = audio_id
                with st.spinner("🔄 讯飞正在识别语音..."):
                    pcm_data = convert_audio_to_pcm16k(audio["bytes"])
                    text, err = xunfei_speech_to_text(pcm_data)
                if text:
                    st.session_state.recognized_text_p1 = text
                    st.session_state.last_pcm_p1 = pcm_data
                    st.session_state.answer_text_p1 = text
                    st.rerun()
                else:
                    st.warning(f"语音识别未成功：{err}，请重新录音或手动输入")

        if st.session_state.recognized_text_p1:
            st.success("✅ 识别成功！")
            st.markdown(f'<div class="question-card" style="background:#f0f8e8">📝 识别结果：{st.session_state.recognized_text_p1}</div>', unsafe_allow_html=True)

        st.markdown("**或者手动输入回答：**")
    else:
        st.info("🎙️ 请检查讯飞配置")

    if "answer_text_p1" not in st.session_state:
        st.session_state.answer_text_p1 = ""
    if st.session_state.recognized_text_p1 and not st.session_state.answer_text_p1:
        st.session_state.answer_text_p1 = st.session_state.recognized_text_p1
    answer = st.text_area("输入你的口语回答（英文）", height=120, placeholder="Type your answer in English...", key="answer_text_p1")

    if ref_answer:
        with st.expander("💡 查看参考答案"):
            st.write("**参考回答：**")
            st.write(ref_answer)
            if tips:
                st.write(tips)

    if st.button("📊 提交评分", key="submit_speaking"):
        if not answer.strip():
            st.warning("请先输入回答内容！")
        else:
            with st.spinner("Embo 正在评分中..."):
                result = score_speaking_p1(question, answer)
            if result:
                pron = st.session_state.get("pronunciation_score", None)
                if pron:
                    result["pronunciation"] = pron
                else:
                    result["pronunciation"] = None
                st.session_state.score_result = result
                st.session_state.page = "speaking_result"; st.rerun()
            else:
                st.error("评分失败，请稍后重试")


# ── 口语Part2流程 ─────────────────────────────────────────
def page_speaking_part2():
    st.markdown('<div class="embo-header"><div class="embo-header-title">🎤 Part 2 · 长篇独白</div></div>', unsafe_allow_html=True)
    if st.button("← 返回陪练", key="back_part2"):
        st.session_state.page = "speaking_practice"; st.rerun()
    item = st.session_state.get("current_p23_item", {})
    cue_card = item.get("cue_card", [])
    topic = item.get("topic", "")
    cue_text = "<br>".join(cue_card) if cue_card else ""
    st.markdown(f"""
    <div class="question-card">
        <div style="font-size:12px;color:#639922;margin-bottom:8px">📋 Part 2 Cue Card</div>
        <b>{topic}</b><br><br>{cue_text}
    </div>
    """, unsafe_allow_html=True)

    if MIC_AVAILABLE and XUNFEI_CONFIGS:
        p2_audio = mic_recorder(start_prompt="🔴 开始录音", stop_prompt="⏹️ 停止录音", just_once=True, key="recorder_part2")
        if p2_audio and p2_audio.get("bytes"):
            p2_audio_id = len(p2_audio["bytes"])
            if st.session_state.get("p2_audio_id") != p2_audio_id:
                st.session_state["p2_audio_id"] = p2_audio_id
                with st.spinner("🔄 识别语音..."):
                    p2_pcm = convert_audio_to_pcm16k(p2_audio["bytes"])
                    p2_text, _ = xunfei_speech_to_text(p2_pcm)
                if p2_text:
                    st.session_state["p2_recognized"] = p2_text
                    st.rerun()
        if st.session_state.get("p2_recognized"):
            st.success(f"✅ 识别：{st.session_state['p2_recognized']}")
    if "p2_recognized" in st.session_state and st.session_state["p2_recognized"]:
        if "p2_answer_input" not in st.session_state or not st.session_state.get("p2_answer_input"):
            st.session_state["p2_answer_input"] = st.session_state["p2_recognized"]
    answer = st.text_area("输入你的Part 2回答（英文）", height=180, placeholder="Describe the place/person/experience in detail...", key="p2_answer_input")

    if st.button("✅ 完成Part 2，进入Part 3 →", key="submit_part2"):
        rec_p2 = st.session_state.get("p2_recognized", "").strip()
        typ_p2 = st.session_state.get("p2_answer_input", "").strip()
        final = rec_p2 or typ_p2
        if not final:
            st.warning("请先录音或输入内容！")
        else:
            with st.spinner("考官正在过渡到Part 3..."):
                transition = call_deepseek("你是雅思口语考官，用英文生成一句自然的过渡语，从Part 2过渡到Part 3，根据考生回答内容生成，简短自然。",
                                           f"考生Part 2回答：{final}\n\n请生成过渡语。")
            st.session_state.part2_answer = final
            st.session_state.part3_transition = transition
            st.session_state.part3_index = 0
            st.session_state.part3_answers = []
            st.session_state.part3_followups = []
            st.session_state.page = "speaking_part3"; st.rerun()


# ── 口语Part3流程 ─────────────────────────────────────────
def page_speaking_part3():
    st.markdown('<div class="embo-header"><div class="embo-header-title">🎤 Part 3 · 深度讨论</div></div>', unsafe_allow_html=True)
    item = st.session_state.get("current_p23_item", {})
    part3_qs = item.get("part3", [])
    idx = st.session_state.get("part3_index", 0)
    transition = st.session_state.get("part3_transition", "")
    followups = st.session_state.get("part3_followups", [])

    if idx == 0 and transition:
        st.markdown(f"""
        <div style="background:#EAF3DE;border:1px solid #C0DD97;border-radius:0 12px 12px 12px;padding:12px 14px;margin-bottom:16px;">
            <div style="font-size:11px;color:#639922;margin-bottom:4px;font-weight:600">🎙️ 考官过渡语</div>
            <div style="font-size:14px;color:#27500A;line-height:1.6">{transition}</div>
        </div>
        """, unsafe_allow_html=True)

    total = len(part3_qs)
    st.markdown(f"<div style='font-size:12px;color:#639922;margin-bottom:8px'>Part 3 进度：{min(idx+1, total)} / {total}</div>", unsafe_allow_html=True)

    for i, (q, a, fu) in enumerate(zip(part3_qs[:idx], st.session_state.get("part3_answers", []), followups)):
        with st.expander(f"✅ 问题{i+1}：{q[:30]}..."):
            st.write(f"**你的回答：** {a}")
            if fu:
                st.write(f"**考官追问：** {fu}")

    if idx < len(part3_qs):
        current_q = part3_qs[idx]
        if followups and len(followups) > len(st.session_state.get("part3_answers", [])):
            current_q = followups[-1]
            st.markdown(f'<div class="question-card"><div style="font-size:11px;color:#639922;margin-bottom:4px">🔄 追问</div><b>{current_q}</b></div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="question-card"><div style="font-size:11px;color:#639922;margin-bottom:4px">问题 {idx+1}</div><b>Q: {current_q}</b></div>', unsafe_allow_html=True)

        if MIC_AVAILABLE and XUNFEI_CONFIGS:
            p3_mic = mic_recorder(start_prompt="🔴 开始录音", stop_prompt="⏹️ 停止录音", just_once=True, key=f"rec_p3_{idx}")
            if p3_mic and p3_mic.get("bytes"):
                p3_aid = len(p3_mic["bytes"])
                if st.session_state.get(f"p3_aid_{idx}") != p3_aid:
                    st.session_state[f"p3_aid_{idx}"] = p3_aid
                    with st.spinner("🔄 识别中..."):
                        p3_pcm = convert_audio_to_pcm16k(p3_mic["bytes"])
                        p3_txt, _ = xunfei_speech_to_text(p3_pcm)
                    if p3_txt:
                        st.session_state[f"p3_txt_{idx}"] = p3_txt
                        st.rerun()
            if st.session_state.get(f"p3_txt_{idx}"):
                st.success(f"✅ {st.session_state[f'p3_txt_{idx}']}")

        answer = st.text_area("输入你的回答（英文）", height=100,
                              value=st.session_state.get(f"p3_txt_{idx}", ""),
                              placeholder="Give a detailed answer...", key=f"p3_answer_{idx}")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("📤 提交回答", key=f"submit_p3_{idx}"):
                typed = st.session_state.get(f"p3_answer_{idx}", "").strip()
                final_p3 = typed or "（已回答）"
                answers = st.session_state.get("part3_answers", [])
                answers.append(final_p3)
                st.session_state.part3_answers = answers
                with st.spinner("考官正在回应..."):
                    fu = call_deepseek("你是雅思口语考官。回答正常就回复NEXT，否则追问一句英文。只输出追问或NEXT。",
                                       f"题目：{current_q}\n考生回答：{final_p3}")
                followups_list = st.session_state.get("part3_followups", [])
                if fu.strip().upper().startswith("NEXT") or len(final_p3.split()) > 20:
                    followups_list.append("")
                    st.session_state.part3_followups = followups_list
                    st.session_state.part3_index = idx + 1
                else:
                    followups_list.append(fu.strip())
                    st.session_state.part3_followups = followups_list
                st.rerun()
        with col2:
            if st.button("⏭️ 跳过此题", key=f"skip_p3_{idx}"):
                answers = st.session_state.get("part3_answers", [])
                answers.append("（跳过）")
                st.session_state.part3_answers = answers
                followups_list = st.session_state.get("part3_followups", [])
                followups_list.append("")
                st.session_state.part3_followups = followups_list
                st.session_state.part3_index = idx + 1
                st.rerun()
    else:
        st.success("✅ Part 3 全部完成！")
        if st.button("📊 获取综合评分", key="get_final_score"):
            with st.spinner("Embo 正在综合评分..."):
                part2_ans = st.session_state.get("part2_answer", "")
                part3_ans = st.session_state.get("part3_answers", [])
                all_text = f"Part 2回答：{part2_ans}\n\nPart 3回答：{chr(10).join(part3_ans)}"
                result = score_speaking_overall(all_text)
            if result:
                result["pronunciation"] = "需录音功能支持"
                st.session_state.score_result = result
                st.session_state.page = "speaking_result"; st.rerun()
            else:
                st.error("评分失败，请稍后重试")


# ── 口语评分结果 ──────────────────────────────────────────
def page_speaking_result():
    st.markdown('<div class="embo-header"><div class="embo-header-title">📊 口语评分结果</div></div>', unsafe_allow_html=True)
    result = st.session_state.score_result
    if not result:
        st.error("没有评分结果"); return
    overall = result.get("overall", 0)
    st.markdown(f"""
    <div class="score-big">
        <div class="score-number">{overall}</div>
        <div class="score-label">口语综合评分（满分 9.0）</div>
    </div>
    """, unsafe_allow_html=True)
    for name, key, color in [("Fluency & Coherence","fluency","#639922"),("Lexical Resource","lexical","#639922"),("Grammatical Range","grammar","#639922")]:
        score = result.get(key, 0)
        pct = int(score / 9 * 100)
        st.markdown(f"""
        <div class="dim-card">
            <div class="dim-row"><span class="dim-name">{name}</span><span class="dim-score">{score}</span></div>
            <div class="bar-bg"><div class="bar-fill" style="width:{pct}%;background:{color}"></div></div>
        </div>
        """, unsafe_allow_html=True)
    pron = result.get("pronunciation", None)
    if pron and isinstance(pron, (int, float)):
        pct = int(pron / 9 * 100)
        st.markdown(f"""
        <div class="dim-card">
            <div class="dim-row"><span class="dim-name">Pronunciation</span><span class="dim-score">{pron}</span></div>
            <div class="bar-bg"><div class="bar-fill" style="width:{pct}%;background:#1D9E75"></div></div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="dim-card">
            <div class="dim-row"><span class="dim-name">Pronunciation</span>
            <span style="font-size:12px;color:#88a870">请使用录音功能获取发音评分</span></div>
            <div class="bar-bg"></div>
        </div>
        """, unsafe_allow_html=True)
    st.markdown(f"""
    <div class="feedback-good">
        <div class="feedback-title" style="color:#3B6D11">✅ 优点</div>
        <div class="feedback-text">{result.get('strengths','')}</div>
    </div>
    <div class="feedback-improve">
        <div class="feedback-title" style="color:#854F0B">💡 改进建议</div>
        <div class="feedback-text">{result.get('improvements','')}</div>
    </div>
    """, unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 再练一题", key="retry_speaking"):
            st.session_state.page = "speaking_practice"; st.rerun()
    with col2:
        if st.button("🏠 返回主页", key="home_speaking"):
            st.session_state.page = "home"; st.rerun()


# ── 模拟考试 ──────────────────────────────────────────────
def page_speaking_exam():
    st.markdown('<div class="embo-header"><div class="embo-header-title">🏆 模拟考试</div></div>', unsafe_allow_html=True)
    if st.button("← 返回主页", key="back_exam"):
        st.session_state.page = "home"; st.rerun()

    exam_phase = st.session_state.get("exam_phase", "setup")

    if exam_phase == "setup":
        difficulty = st.radio("选择难度", ["基础", "标准", "挑战"], horizontal=True)
        st.markdown("""
        <div class="dim-card">
            <div style="font-size:14px;font-weight:600;color:#27500A;margin-bottom:12px">考试流程说明</div>
            <div style="font-size:13px;color:#555;line-height:2">
                📌 <b>Part 1</b> · 热身问答（3-4个问题）<br>
                📌 <b>Part 2</b> · 长篇独白（1分钟准备 + 2分钟作答）<br>
                📌 <b>Part 3</b> · 深度讨论，AI会追问（3-4个问题）
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("🚀 开始模拟考试", key="start_exam"):
            bank = st.session_state.question_bank
            topics = bank.get("speaking_p1_current", []) or bank.get("speaking_p1_past", [])
            part1_questions = []
            if topics:
                selected_topics = random.sample(topics, min(2, len(topics)))
                for t in selected_topics:
                    qs = t.get("questions", [])
                    if qs:
                        q = random.choice(qs)
                        part1_questions.append({"topic": t.get("topic",""), "question": q.get("question",""),
                                                "answer": q.get("answer",""), "tips": q.get("tips","")})
            items = bank.get("speaking_p23_current", []) or bank.get("speaking_p23_past", [])
            p23_item = random.choice(items) if items else {}
            st.session_state.exam_difficulty = difficulty
            st.session_state.exam_part1_questions = part1_questions
            st.session_state.exam_part1_index = 0
            st.session_state.exam_part1_answers = []
            st.session_state.current_p23_item = p23_item
            st.session_state.exam_phase = "part1"
            st.rerun()

    elif exam_phase == "part1":
        questions = st.session_state.get("exam_part1_questions", [])
        idx = st.session_state.get("exam_part1_index", 0)
        total = len(questions)
        st.markdown(f"<div style='font-size:12px;color:#639922;margin-bottom:8px'>📌 Part 1 · 问题 {idx+1} / {total}</div>", unsafe_allow_html=True)
        st.progress((idx) / max(total, 1), text=f"Part 1 进行中")
        st.warning("⏱️ 每题限时约1分钟，请简短回答")
        if idx < total:
            q_data = questions[idx]
            st.markdown(f"""
            <div class="question-card">
                <div style="font-size:11px;color:#639922;margin-bottom:4px">话题：{q_data.get('topic','')}</div>
                <b>Q: {q_data.get('question','')}</b>
            </div>
            """, unsafe_allow_html=True)
            if MIC_AVAILABLE and XUNFEI_CONFIGS:
                exam1_audio = mic_recorder(start_prompt="🔴 开始录音", stop_prompt="⏹️ 停止录音", just_once=True, key=f"exam_rec_p1_{idx}")
                if exam1_audio and exam1_audio.get("bytes"):
                    e1_aid = len(exam1_audio["bytes"])
                    if st.session_state.get(f"e1_aid_{idx}") != e1_aid:
                        st.session_state[f"e1_aid_{idx}"] = e1_aid
                        with st.spinner("🔄 识别中..."):
                            e1_pcm = convert_audio_to_pcm16k(exam1_audio["bytes"])
                            e1_text, _ = xunfei_speech_to_text(e1_pcm)
                        if e1_text:
                            st.session_state["current_rec_ep1"] = e1_text
                            st.session_state["current_rec_ep1_idx"] = idx
                            st.rerun()
                if st.session_state.get("current_rec_ep1_idx") == idx and st.session_state.get("current_rec_ep1"):
                    st.success(f"✅ {st.session_state['current_rec_ep1']}")
            answer = st.text_area("输入你的回答（英文）", height=80, placeholder="Answer briefly and naturally...", key=f"exam_p1_{idx}")
            if st.button("下一题 →", key=f"exam_next_{idx}"):
                recognized1 = st.session_state.get("current_rec_ep1", "").strip()
                typed1 = st.session_state.get(f"exam_p1_{idx}", "").strip()
                final_ans = recognized1 or typed1 or "（已回答）"
                st.session_state["current_rec_ep1"] = ""
                answers = st.session_state.get("exam_part1_answers", [])
                answers.append(final_ans)
                st.session_state.exam_part1_answers = answers
                st.session_state.exam_part1_index = idx + 1
                st.rerun()
        else:
            st.success("✅ Part 1 完成！")
            if st.button("进入 Part 2 →", key="go_part2"):
                st.session_state.part2_answer = ""
                st.session_state.part3_transition = ""
                st.session_state.part3_index = 0
                st.session_state.part3_answers = []
                st.session_state.part3_followups = []
                st.session_state.exam_phase = "part2"
                st.rerun()

    elif exam_phase == "part2":
        item = st.session_state.get("current_p23_item", {})
        cue_card = item.get("cue_card", [])
        topic = item.get("topic", "")
        cue_text = "<br>".join(cue_card) if cue_card else ""
        st.markdown(f"""
        <div class="question-card">
            <div style="font-size:12px;color:#639922;margin-bottom:8px">📌 Part 2 · 长篇独白</div>
            <b>{topic}</b><br><br>{cue_text}
        </div>
        """, unsafe_allow_html=True)
        st.warning("⏱️ Part 2 限时：1分钟准备 + 2分钟作答")
        if MIC_AVAILABLE and XUNFEI_CONFIGS:
            ep2_audio = mic_recorder(start_prompt="🔴 开始录音", stop_prompt="⏹️ 停止录音", just_once=True, key="exam_rec_p2")
            if ep2_audio and ep2_audio.get("bytes"):
                ep2_aid = len(ep2_audio["bytes"])
                if st.session_state.get("ep2_aid") != ep2_aid:
                    st.session_state["ep2_aid"] = ep2_aid
                    with st.spinner("🔄 识别中..."):
                        ep2_pcm = convert_audio_to_pcm16k(ep2_audio["bytes"])
                        ep2_txt, _ = xunfei_speech_to_text(ep2_pcm)
                    if ep2_txt:
                        st.session_state["ep2_txt"] = ep2_txt
                        st.session_state["exam_part2_answer"] = ep2_txt
                        st.rerun()
            if st.session_state.get("ep2_txt"):
                st.success(f"✅ {st.session_state['ep2_txt']}")
        answer = st.text_area("输入你的Part 2回答（英文）", height=160, placeholder="Describe in detail...", key="exam_part2_answer")
        if st.button("✅ 完成Part 2，进入Part 3 →", key="exam_submit_part2"):
            recognized2 = st.session_state.get("ep2_txt", "").strip()
            typed2 = st.session_state.get("exam_part2_answer", "").strip()
            final_ep2 = recognized2 or typed2
            if not final_ep2:
                st.warning("请先录音或输入内容！")
            else:
                with st.spinner("考官正在过渡..."):
                    transition = call_deepseek("你是雅思考官，用英文生成一句简短自然的过渡语，从Part 2过渡到Part 3。",
                                               f"考生Part 2回答：{final_ep2}")
                st.session_state.part2_answer = final_ep2
                st.session_state.part3_transition = transition
                st.session_state.part3_index = 0
                st.session_state.part3_answers = []
                st.session_state.part3_followups = []
                st.session_state.exam_phase = "part3"
                st.rerun()

    elif exam_phase == "part3":
        item = st.session_state.get("current_p23_item", {})
        part3_qs = item.get("part3", [])
        idx = st.session_state.get("part3_index", 0)
        transition = st.session_state.get("part3_transition", "")
        followups = st.session_state.get("part3_followups", [])

        if idx == 0 and transition:
            st.markdown(f"""
            <div style="background:#EAF3DE;border:1px solid #C0DD97;border-radius:0 12px 12px 12px;padding:12px 14px;margin-bottom:16px;">
                <div style="font-size:11px;color:#639922;margin-bottom:4px;font-weight:600">🎙️ 考官过渡语</div>
                <div style="font-size:14px;color:#27500A">{transition}</div>
            </div>
            """, unsafe_allow_html=True)

        total = len(part3_qs)
        st.markdown(f"<div style='font-size:12px;color:#639922;margin-bottom:8px'>📌 Part 3 · 问题 {min(idx+1,total)} / {total}</div>", unsafe_allow_html=True)

        if idx < total:
            current_q = part3_qs[idx]
            if followups and len(followups) > len(st.session_state.get("part3_answers",[])):
                current_q = followups[-1]
                st.markdown(f'<div class="question-card"><div style="font-size:11px;color:#639922;margin-bottom:4px">🔄 追问</div><b>{current_q}</b></div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="question-card"><div style="font-size:11px;color:#639922;margin-bottom:4px">问题 {idx+1}</div><b>Q: {current_q}</b></div>', unsafe_allow_html=True)
                if MIC_AVAILABLE and XUNFEI_CONFIGS:
                    exam3_audio = mic_recorder(start_prompt="🔴 开始录音", stop_prompt="⏹️ 停止录音", just_once=True, key=f"exam_rec_p3_{idx}")
                    if exam3_audio and exam3_audio.get("bytes"):
                        e3_aid = len(exam3_audio["bytes"])
                        if st.session_state.get(f"e3_aid_{idx}") != e3_aid:
                            st.session_state[f"e3_aid_{idx}"] = e3_aid
                            with st.spinner("🔄 识别中..."):
                                e3_pcm = convert_audio_to_pcm16k(exam3_audio["bytes"])
                                e3_text, _ = xunfei_speech_to_text(e3_pcm)
                            if e3_text:
                                st.session_state[f"exam_p3_text_{idx}"] = e3_text
                                st.rerun()
                    if st.session_state.get(f"exam_p3_text_{idx}"):
                        st.success(f"✅ {st.session_state[f'exam_p3_text_{idx}']}")
            answer = st.text_area("输入你的回答（英文）", height=80,
                                  value=st.session_state.get(f"ep3_txt_{idx}", ""),
                                  placeholder="Give a detailed answer...", key=f"exam_p3_{idx}")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("📤 提交回答", key=f"exam_submit_p3_{idx}"):
                    typed3 = st.session_state.get(f"exam_p3_{idx}", "").strip()
                    final3 = typed3 or "（已回答）"
                    answers = st.session_state.get("part3_answers", [])
                    answers.append(final3)
                    st.session_state.part3_answers = answers
                    with st.spinner("考官正在回应..."):
                        fu = call_deepseek("你是雅思口语考官。回答正常就回复NEXT，否则追问一句英文。只输出追问或NEXT。",
                                           f"题目：{current_q}\n回答：{final3}")
                    followups_list = st.session_state.get("part3_followups", [])
                    if fu.strip().upper().startswith("NEXT") or len(final3.split()) > 15:
                        followups_list.append("")
                        st.session_state.part3_followups = followups_list
                        st.session_state.part3_index = idx + 1
                    else:
                        followups_list.append(fu.strip())
                        st.session_state.part3_followups = followups_list
                    st.rerun()
            with col2:
                if st.button("⏭️ 跳过", key=f"exam_skip_p3_{idx}"):
                    answers = st.session_state.get("part3_answers", [])
                    answers.append("（跳过）")
                    st.session_state.part3_answers = answers
                    followups_list = st.session_state.get("part3_followups", [])
                    followups_list.append("")
                    st.session_state.part3_followups = followups_list
                    st.session_state.part3_index = idx + 1
                    st.rerun()
        else:
            st.success("🎉 模拟考试全部完成！")
            if st.button("📊 获取综合评分", key="exam_final_score"):
                with st.spinner("Embo 正在综合评分..."):
                    p1_ans = st.session_state.get("exam_part1_answers", [])
                    p2_ans = st.session_state.get("part2_answer", "")
                    p3_ans = st.session_state.get("part3_answers", [])
                    all_text = f"Part1回答：{chr(10).join(p1_ans)}\n\nPart2回答：{p2_ans}\n\nPart3回答：{chr(10).join(p3_ans)}"
                    result = score_speaking_overall(all_text)
                if result:
                    result["pronunciation"] = "需录音功能支持"
                    st.session_state.score_result = result
                    st.session_state.exam_phase = "setup"
                    st.session_state.page = "speaking_result"; st.rerun()
                else:
                    st.error("评分失败，请稍后重试")


# ── 自由对话陪练 ──────────────────────────────────────────
def page_speaking_free_chat():
    st.markdown('<div class="embo-header"><div class="embo-header-title">💬 自由对话陪练</div></div>', unsafe_allow_html=True)
    if st.button("← 返回陪练", key="back_free_chat"):
        st.session_state.page = "speaking_practice"; st.rerun()
    if not st.session_state.free_chat_history:
        with st.spinner("Embo 考官正在准备..."):
            first_q = call_deepseek("你是专业的雅思口语考官，用英文与考生进行口语练习。从Part 1开始，问日常问题，每次只问一个，不评分，只提问。",
                                    "请开始口语练习，问第一个问题。")
        st.session_state.free_chat_history = [{"role": "examiner", "content": first_q}]
    for msg in st.session_state.free_chat_history:
        if msg["role"] == "examiner":
            st.markdown(f"""
            <div style="background:#EAF3DE;border:1px solid #C0DD97;border-radius:0 12px 12px 12px;padding:12px 14px;margin-bottom:10px;">
                <div style="font-size:11px;color:#639922;margin-bottom:4px;font-weight:600">🎙️ Embo 考官</div>
                <div style="font-size:14px;color:#27500A;line-height:1.6">{msg["content"]}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style="background:white;border:1px solid #e0edd0;border-radius:12px 0 12px 12px;padding:12px 14px;margin-bottom:10px;margin-left:20px;">
                <div style="font-size:11px;color:#88a870;margin-bottom:4px">你的回答</div>
                <div style="font-size:14px;color:#333;line-height:1.6">{msg["content"]}</div>
            </div>
            """, unsafe_allow_html=True)
    user_input = st.text_area("输入你的回答（英文）", height=100, placeholder="Type your answer in English...", key="free_chat_input")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📤 发送回答", key="send_answer"):
            if user_input.strip():
                st.session_state.free_chat_history.append({"role": "user", "content": user_input})
                with st.spinner("考官正在回应..."):
                    history_text = "\n".join([f"{'考官' if m['role']=='examiner' else '考生'}：{m['content']}" for m in st.session_state.free_chat_history])
                    next_q = call_deepseek("你是雅思口语考官，根据对话继续提一个问题，英文回复。", f"对话历史：\n{history_text}\n请继续。")
                st.session_state.free_chat_history.append({"role": "examiner", "content": next_q})
                st.rerun()
    with col2:
        if st.button("🏁 结束对话", key="end_chat"):
            st.session_state.free_chat_history = []
            st.session_state.page = "speaking_practice"; st.rerun()


# ── 路由 ──────────────────────────────────────────────────
pages = {
    "welcome": page_welcome, "home": page_home,
    "writing_direct": page_writing_direct, "writing_practice": page_writing_practice,
    "writing_answer": page_writing_answer, "writing_result": page_writing_result,
    "speaking_practice": page_speaking_practice, "speaking_record": page_speaking_record,
    "speaking_part2": page_speaking_part2, "speaking_part3": page_speaking_part3,
    "speaking_result": page_speaking_result, "speaking_exam": page_speaking_exam,
    "speaking_free_chat": page_speaking_free_chat,
}
pages.get(st.session_state.page, page_welcome)()