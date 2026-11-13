import AVFoundation
import Observation
import Speech

/// Talking to FRIDAY: dictation into the chat box, and FRIDAY saying where a ticket stands.
/// Both use the Mac's own speech services; dictation stays on the Mac when the language supports it.
@MainActor
@Observable
final class Voice {
    var listening = false
    var transcript = ""
    var problem: String?

    @ObservationIgnored private let engine = AVAudioEngine()
    @ObservationIgnored private let synthesizer = AVSpeechSynthesizer()
    @ObservationIgnored private var recognizer: SFSpeechRecognizer?
    @ObservationIgnored private var request: SFSpeechAudioBufferRecognitionRequest?
    @ObservationIgnored private var task: SFSpeechRecognitionTask?

    private var language: String {
        UserDefaults.standard.string(forKey: Preference.voiceLanguage) ?? Preference.defaultVoiceLanguage
    }

    /// macOS ends an app that asks for the microphone without saying why, so dictation is only offered
    /// when the app bundle carries those texts (build-app.sh writes them).
    private var canAskForPermission: Bool {
        Bundle.main.object(forInfoDictionaryKey: "NSSpeechRecognitionUsageDescription") != nil
            && Bundle.main.object(forInfoDictionaryKey: "NSMicrophoneUsageDescription") != nil
    }

    func toggle() {
        if listening { stop() } else { start() }
    }

    func start() {
        problem = nil
        guard canAskForPermission else {
            problem = "Dictation needs the app: run friday/build-app.sh, then open FRIDAY.app."
            return
        }
        SFSpeechRecognizer.requestAuthorization { status in
            guard status == .authorized else {
                Task { @MainActor in self.problem = "Allow Speech Recognition for FRIDAY in System Settings › Privacy & Security." }
                return
            }
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                Task { @MainActor in
                    if granted {
                        self.listen()
                    } else {
                        self.problem = "Allow the microphone for FRIDAY in System Settings › Privacy & Security."
                    }
                }
            }
        }
    }

    private func listen() {
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: language)), recognizer.isAvailable else {
            problem = "Speech recognition is not available for \(language). Change the voice language in Settings."
            return
        }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 else {
            problem = "No microphone found."
            return
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }
        do {
            engine.prepare()
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            problem = "Could not start the microphone: \(error.localizedDescription)"
            return
        }
        self.recognizer = recognizer
        self.request = request
        transcript = ""
        listening = true
        task = recognizer.recognitionTask(with: request) { result, error in
            let heard = result?.bestTranscription.formattedString
            let finished = error != nil || result?.isFinal == true
            Task { @MainActor in
                if let heard { self.transcript = heard }
                if finished {
                    self.stop()
                    self.task = nil
                }
            }
        }
    }

    func stop() {
        guard listening else { return }
        listening = false
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        request = nil
    }

    /// Stops listening and drops what was still being recognised, so it cannot arrive after the message is sent.
    func cancel() {
        task?.cancel()
        task = nil
        stop()
    }

    func speak(_ text: String) {
        synthesizer.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: language)
        synthesizer.speak(utterance)
    }
}
