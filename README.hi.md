# SCADA Generator

[Русский](README.md) · [English](README.en.md) · [中文](README.zh.md) · **हिन्दी** · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Italiano](README.it.md)

## परिचय

YAML से कॉन्फ़िगर किया गया औद्योगिक निगरानी प्रोटोटाइप: डेटा संग्रह,
स्वतः निर्मित HMI, अलार्म जीवनचक्र और C++17/Python में स्ट्रीम विश्लेषण।
डेमो में अनुकरण किया गया पंप स्टेशन और स्थानीय Modbus TCP सर्वर उपयोग होते हैं।
सिमुलेशन परीक्षण वास्तविक औद्योगिक तैनाती, फील्ड सटीकता या स्वतंत्र प्रोटोकॉल प्रमाणन सिद्ध नहीं करते।

## मुख्य घटक

- Modbus TCP/RTU, OPC UA, MQTT और IEC 60870-5-104 अडैप्टर।
- `configs/config.yaml` से HMI निर्माण।
- हिस्टेरिसिस, विलंब, स्वीकृति और स्थगन वाले अलार्म।
- pybind11 के माध्यम से C++17 में मजबूत EWMA डिटेक्टर और Holt प्रवृत्ति पूर्वानुमान।
- बहुचर PCA/MSPC विश्लेषण और समानता परीक्षणों वाला Python विकल्प।
- REST API, WebSocket और माइग्रेशन वाला PostgreSQL संग्रह।

## त्वरित शुरुआत

```bash
pip install -r requirements.txt
python run.py --demo --open
```

`http://127.0.0.1:8000` खोलें। डेमो मोड में PostgreSQL आवश्यक नहीं है।
परिदृश्यों में खराबियाँ डाली जा सकती हैं; उनके परिणाम सिमुलेटर तक सीमित हैं।

## कॉन्फ़िगरेशन और अडैप्टर

उपकरण और संकेत `configs/config.yaml` में परिभाषित हैं।
प्रोटोकॉल के अनुसार पता फ़ील्ड `address`, `node`, `topic` या `ioa` होता है।
वैकल्पिक लाइब्रेरी: `pip install -r requirements-protocols.txt`।
[बहु-प्रोटोकॉल उदाहरण](configs/examples/multi_protocol.yaml) देखें।
`python run.py --check` बिना लिखने के कनेक्शन और पढ़ने की जाँच करता है।

## बिल्ड और परीक्षण

```bash
pip install -r requirements-dev.txt
python scripts/build_native.py
python -m pytest -q
SCADA_FORCE_PYTHON_CORE=1 python -m pytest -q
ruff check .
ruff format --check .
```

PostgreSQL परीक्षणों के लिए `SCADA_TEST_PG` से परीक्षण डेटाबेस कॉन्फ़िगर करें।
पूरी तकनीकी जानकारी [English](README.en.md) में है।

## सुरक्षा और सीमाएँ

डिफ़ॉल्ट पता `127.0.0.1` है। नियंत्रण के लिए `SCADA_API_TOKEN` सेट करें:
टोकन सेट होने पर कमांड में `X-API-Token` आवश्यक है।
लेखन केवल `writable: true` संकेतों और `min..max` सीमा में अनुमत है।
एक नोड, बिना रेडंडेंसी; एक ऑपरेटर टोकन, बिना भूमिकाओं के। OPC UA में polling है।
पूर्व-अलार्म प्रवृत्ति का विस्तार करते हैं; अचानक विफलताओं की भविष्यवाणी नहीं करते।
प्रत्येक उपकरण के साथ अनुकूलता अलग से जाँचनी होगी।
कोर की गति पूरी प्रणाली की गति नहीं है।
एप्लिकेशन इंटरफ़ेस रूसी, अंग्रेज़ी और चीनी में है; आठ भाषाएँ दस्तावेज़ों के लिए हैं।

## दस्तावेज़ और लाइसेंस

यह संक्षिप्त मार्गदर्शिका है। विस्तृत संदर्भ: [English](README.en.md) और
[Русский](README.md)। बदलाव: [CHANGELOG.md](CHANGELOG.md)।
MIT: [LICENSE.md](LICENSE.md)। लेखक: Anton Sergeev।
