import CoreML
import FluidAudio
import Foundation

private struct Request: Decodable {
    let type: String
    let sampleRate: Int?
    let variant: String?
    let startMs: Int?
    let pcm16Base64: String?

    enum CodingKeys: String, CodingKey {
        case type
        case sampleRate = "sample_rate"
        case variant
        case startMs = "start_ms"
        case pcm16Base64 = "pcm16_base64"
    }
}

private struct SpeakerTurn: Encodable {
    let speakerID: String
    let startMs: Int
    let endMs: Int
    let confidence: Float

    enum CodingKeys: String, CodingKey {
        case speakerID = "speaker_id"
        case startMs = "start_ms"
        case endMs = "end_ms"
        case confidence
    }
}

private struct Response: Encodable {
    let type: String
    let variant: String?
    let model: String?
    let code: String?
    let message: String?
    let turns: [SpeakerTurn]?
    let fraction: Double?

    init(
        type: String,
        variant: String? = nil,
        model: String? = nil,
        code: String? = nil,
        message: String? = nil,
        turns: [SpeakerTurn]? = nil,
        fraction: Double? = nil
    ) {
        self.type = type
        self.variant = variant
        self.model = model
        self.code = code
        self.message = message
        self.turns = turns
        self.fraction = fraction
    }
}

/// Thread-safe stdout writer for progress callbacks that FluidAudio may invoke
/// off the main actor while a `prepare` request is in flight.
private final class ResponseWriter: @unchecked Sendable {
    private let lock = NSLock()

    func write(_ response: Response) {
        do {
            var data = try JSONEncoder().encode(response)
            data.append(0x0A)
            lock.lock()
            FileHandle.standardOutput.write(data)
            lock.unlock()
        } catch {
            let fallback = "{\"type\":\"error\",\"code\":\"DIARIZATION_PROTOCOL_ERROR\",\"message\":\"编码响应失败\"}\n"
            lock.lock()
            FileHandle.standardOutput.write(Data(fallback.utf8))
            lock.unlock()
        }
    }
}

private enum ServiceError: LocalizedError {
    case invalidRequest(String)
    case modelUnavailable(String)
    case audioInvalid(String)

    var errorDescription: String? {
        switch self {
        case .invalidRequest(let message), .modelUnavailable(let message), .audioInvalid(let message):
            return message
        }
    }

    var code: String {
        switch self {
        case .invalidRequest: return "DIARIZATION_INVALID_REQUEST"
        case .modelUnavailable: return "DIARIZATION_MODEL_UNAVAILABLE"
        case .audioInvalid: return "DIARIZATION_AUDIO_INVALID"
        }
    }
}

@main
struct NemotronDiarizerService {
    private var diarizer: Nemotron3Diarizer?
    private var variant = "low"
    private var streamOriginMs: Int?
    private var streamedFrameCount = 0

    static func main() async {
        var service = NemotronDiarizerService()
        await service.run()
    }

    private mutating func run() async {
        while let line = readLine(strippingNewline: true) {
            guard let data = line.data(using: .utf8) else {
                write(Response(type: "error", code: "DIARIZATION_PROTOCOL_ERROR", message: "输入不是 UTF-8"))
                continue
            }
            do {
                let request = try JSONDecoder().decode(Request.self, from: data)
                switch request.type {
                case "start":
                    try await start(request)
                case "prepare":
                    try await prepare(request)
                case "audio":
                    try processAudio(request)
                case "flush":
                    try flush()
                case "stop":
                    try flush()
                    write(Response(type: "stopped"))
                    return
                default:
                    throw ServiceError.invalidRequest("未知请求类型: \(request.type)")
                }
            } catch let error as ServiceError {
                write(Response(type: "error", code: error.code, message: error.localizedDescription))
            } catch {
                write(Response(type: "error", code: "DIARIZATION_MODEL_UNAVAILABLE", message: error.localizedDescription))
            }
        }
    }

    private mutating func start(_ request: Request) async throws {
        guard request.sampleRate == 16000 else {
            throw ServiceError.invalidRequest("Nemotron 需要 16kHz 单声道音频")
        }
        let requestedVariant = request.variant?.trimmingCharacters(in: .whitespacesAndNewlines)
        variant = requestedVariant?.isEmpty == false ? requestedVariant! : "low"
        guard let config = Nemotron3Config.preset(named: variant) else {
            throw ServiceError.invalidRequest("不支持的 Nemotron variant: \(variant)")
        }
        do {
            let models = try await loadModels(config: config)
            diarizer = Nemotron3Diarizer(config: config, models: models)
            streamOriginMs = nil
            streamedFrameCount = 0
            write(Response(type: "ready", variant: variant, model: "Nemotron-3-Diarization"))
        } catch {
            throw ServiceError.modelUnavailable(error.localizedDescription)
        }
    }

    /// Download and compile the preset without starting a session, so the
    /// settings page can prepare the model ahead of class.
    ///
    /// Progress lines are only legal under `prepare`; the session requests keep
    /// their one-response-per-request contract.
    private func prepare(_ request: Request) async throws {
        let requestedVariant = request.variant?.trimmingCharacters(in: .whitespacesAndNewlines)
        let variant = requestedVariant?.isEmpty == false ? requestedVariant! : "low"
        guard let config = Nemotron3Config.preset(named: variant) else {
            throw ServiceError.invalidRequest("不支持的 Nemotron variant: \(variant)")
        }
        let writer = ResponseWriter()
        do {
            _ = try await Nemotron3Models.loadFromHuggingFace(
                config: config,
                cacheDirectory: modelCacheDirectory(),
                computeUnits: configuredComputeUnits(),
                progressHandler: { progress in
                    writer.write(
                        Response(type: "download_progress", fraction: progress.fractionCompleted)
                    )
                }
            )
        } catch {
            throw ServiceError.modelUnavailable(error.localizedDescription)
        }
        writer.write(Response(type: "ready", variant: variant, model: "Nemotron-3-Diarization"))
    }

    private func loadModels(config: Nemotron3Config) async throws -> Nemotron3Models {
        return try await Nemotron3Models.loadFromHuggingFace(
            config: config,
            cacheDirectory: modelCacheDirectory(),
            computeUnits: configuredComputeUnits()
        )
    }

    private func modelCacheDirectory() -> URL? {
        let environment = ProcessInfo.processInfo.environment
        guard let configured = environment["NEMOTRON_MODEL_DIR"], !configured.isEmpty else {
            return nil
        }
        let url = URL(fileURLWithPath: configured, isDirectory: true)
        if url.lastPathComponent == "nemotron-3-diarization" {
            return url.deletingLastPathComponent()
        }
        return url
    }

    private func configuredComputeUnits() -> MLComputeUnits {
        switch ProcessInfo.processInfo.environment["NEMOTRON_COMPUTE_UNITS"]?.lowercased() {
        case "ane": return .cpuAndNeuralEngine
        case "gpu": return .cpuAndGPU
        case "cpu": return .cpuOnly
        default: return .all
        }
    }

    private mutating func processAudio(_ request: Request) throws {
        guard let diarizer else {
            throw ServiceError.modelUnavailable("模型尚未就绪")
        }
        guard let encoded = request.pcm16Base64,
              let data = Data(base64Encoded: encoded),
              !data.isEmpty else {
            throw ServiceError.audioInvalid("音频数据为空或不是有效 Base64")
        }
        let samples = try decodePCM16(data)
        if streamOriginMs == nil {
            streamOriginMs = max(0, request.startMs ?? 0)
        }
        diarizer.appendAudio(samples)
        let results = try diarizer.processBufferedAudio()
        var turns: [SpeakerTurn] = []
        for result in results {
            turns.append(contentsOf: convert(result, originMs: streamOriginMs ?? 0))
        }
        if !turns.isEmpty {
            write(Response(type: "speaker_turns", turns: turns))
        } else {
            write(Response(type: "speaker_turns", turns: []))
        }
    }

    private mutating func flush() throws {
        guard let diarizer else {
            write(Response(type: "flushed", turns: []))
            return
        }
        var turns: [SpeakerTurn] = []
        for result in try diarizer.finishStream() {
            turns.append(contentsOf: convert(result, originMs: streamOriginMs ?? 0))
        }
        write(Response(type: "speaker_turns", turns: turns))
    }

    private mutating func convert(
        _ result: Nemotron3ChunkResult,
        originMs: Int
    ) -> [SpeakerTurn] {
        let segments = Nemotron3Diarizer.segments(
            probabilities: result.probabilities,
            frameCount: result.frameCount
        )
        let offsetMs = originMs + streamedFrameCount * 10
        streamedFrameCount += result.frameCount
        return segments.map { segment in
            SpeakerTurn(
                speakerID: "speaker_\(segment.speakerIndex)",
                startMs: offsetMs + Int((segment.startSeconds * 1000).rounded()),
                endMs: offsetMs + Int((segment.endSeconds * 1000).rounded()),
                confidence: 0.75
            )
        }
    }

    private func decodePCM16(_ data: Data) throws -> [Float] {
        guard data.count % 2 == 0 else {
            throw ServiceError.audioInvalid("PCM16 字节数必须为偶数")
        }
        return data.withUnsafeBytes { rawBuffer in
            let bytes = rawBuffer.bindMemory(to: UInt8.self)
            var samples = [Float]()
            samples.reserveCapacity(data.count / 2)
            for index in stride(from: 0, to: bytes.count, by: 2) {
                let bits = UInt16(bytes[index]) | (UInt16(bytes[index + 1]) << 8)
                let value = Int16(bitPattern: bits)
                samples.append(Float(value) / 32768.0)
            }
            return samples
        }
    }

    private func write(_ response: Response) {
        do {
            var data = try JSONEncoder().encode(response)
            data.append(0x0A)
            FileHandle.standardOutput.write(data)
        } catch {
            let fallback = "{\"type\":\"error\",\"code\":\"DIARIZATION_PROTOCOL_ERROR\",\"message\":\"编码响应失败\"}\n"
            FileHandle.standardOutput.write(Data(fallback.utf8))
        }
    }
}
