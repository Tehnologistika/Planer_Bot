from vosk import Model, KaldiRecognizer
import wave, json

MODEL = Model("models/vosk-model-small-ru-0.22")

def transcribe_wav(path):
    wf = wave.open(path, "rb")
    rec = KaldiRecognizer(MODEL, wf.getframerate())
    rec.SetWords(True)
    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        rec.AcceptWaveform(data)
    return json.loads(rec.FinalResult())["text"]