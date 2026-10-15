// The device, its two streams and the provider of the LumaLink Camera extension.
//
// Applications read the source stream. LumaLink Studio writes frames into the
// sink stream; the extension passes the latest one on, or the idle image while
// none arrives, like the Windows DirectShow filter in virtualcam/.

import CoreMediaIO
import CoreVideo
import Foundation
import IOKit.audio
import os.log

private let log = Logger(subsystem: CameraConfig.extensionIdentifier, category: "camera")

final class DeviceSource: NSObject, CMIOExtensionDeviceSource {
    private(set) var device: CMIOExtensionDevice!
    private var sourceStream: StreamSource!
    private var sinkStream: StreamSource!

    private let format: CMFormatDescription
    private let idleImage: CVPixelBuffer
    private let queue = DispatchQueue(label: "LumaLink Camera", qos: .userInteractive)

    // Guarded by `queue`.
    private var latestFrame: CVPixelBuffer?
    private var latestFrameTime: TimeInterval = 0
    private var sourceClients = 0
    private var timer: DispatchSourceTimer?
    private var sinkClient: CMIOExtensionClient?

    init(localizedName: String) {
        var description: CMFormatDescription?
        CMVideoFormatDescriptionCreate(
            allocator: kCFAllocatorDefault,
            codecType: kCVPixelFormatType_32BGRA,
            width: CameraConfig.width,
            height: CameraConfig.height,
            extensions: nil,
            formatDescriptionOut: &description
        )
        format = description!
        idleImage = IdleImage.make(width: Int(CameraConfig.width), height: Int(CameraConfig.height))
        super.init()

        device = CMIOExtensionDevice(
            localizedName: localizedName,
            deviceID: CameraConfig.deviceID,
            legacyDeviceID: CameraConfig.deviceID.uuidString,
            source: self
        )
        let frameDuration = CMTime(value: 1, timescale: CameraConfig.frameRate)
        let streamFormat = CMIOExtensionStreamFormat(
            formatDescription: format,
            maxFrameDuration: frameDuration,
            minFrameDuration: frameDuration,
            validFrameDurations: nil
        )
        sourceStream = StreamSource(
            localizedName: "\(localizedName).Video",
            streamID: CameraConfig.sourceStreamID,
            direction: .source,
            format: streamFormat,
            device: self
        )
        sinkStream = StreamSource(
            localizedName: "\(localizedName).Input",
            streamID: CameraConfig.sinkStreamID,
            direction: .sink,
            format: streamFormat,
            device: self
        )
        do {
            try device.addStream(sourceStream.stream)
            try device.addStream(sinkStream.stream)
        } catch {
            fatalError("Failed to add the streams: \(error.localizedDescription)")
        }
    }

    var availableProperties: Set<CMIOExtensionProperty> {
        [.deviceTransportType, .deviceModel]
    }

    func deviceProperties(forProperties properties: Set<CMIOExtensionProperty>) throws -> CMIOExtensionDeviceProperties {
        let deviceProperties = CMIOExtensionDeviceProperties(dictionary: [:])
        if properties.contains(.deviceTransportType) {
            deviceProperties.transportType = kIOAudioDeviceTransportTypeVirtual
        }
        if properties.contains(.deviceModel) {
            deviceProperties.model = CameraConfig.name
        }
        return deviceProperties
    }

    func setDeviceProperties(_ deviceProperties: CMIOExtensionDeviceProperties) throws {}

    // MARK: Source: what applications see

    func startSource() {
        queue.async {
            self.sourceClients += 1
            guard self.timer == nil else { return }
            let timer = DispatchSource.makeTimerSource(flags: .strict, queue: self.queue)
            timer.schedule(deadline: .now(), repeating: 1.0 / Double(CameraConfig.frameRate), leeway: .milliseconds(2))
            // The device lives as long as the extension process.
            timer.setEventHandler { self.sendFrame() }
            timer.resume()
            self.timer = timer
        }
    }

    func stopSource() {
        queue.async {
            self.sourceClients = max(0, self.sourceClients - 1)
            if self.sourceClients == 0 {
                self.timer?.cancel()
                self.timer = nil
            }
        }
    }

    private func sendFrame() {
        let now = ProcessInfo.processInfo.systemUptime
        var image = idleImage
        if let frame = latestFrame, now - latestFrameTime <= CameraConfig.frameTimeout {
            image = frame
        }
        var timing = CMSampleTimingInfo(
            duration: CMTime(value: 1, timescale: CameraConfig.frameRate),
            presentationTimeStamp: CMClockGetTime(CMClockGetHostTimeClock()),
            decodeTimeStamp: .invalid
        )
        var sampleBuffer: CMSampleBuffer?
        let status = CMSampleBufferCreateForImageBuffer(
            allocator: kCFAllocatorDefault,
            imageBuffer: image,
            dataReady: true,
            makeDataReadyCallback: nil,
            refcon: nil,
            formatDescription: format,
            sampleTiming: &timing,
            sampleBufferOut: &sampleBuffer
        )
        guard status == noErr, let sampleBuffer else {
            log.error("Could not wrap a frame: \(status)")
            return
        }
        sourceStream.stream.send(
            sampleBuffer,
            discontinuity: [],
            hostTimeInNanoseconds: UInt64(timing.presentationTimeStamp.seconds * Double(NSEC_PER_SEC))
        )
    }

    // MARK: Sink: frames from LumaLink Studio

    func authorizeSink(for client: CMIOExtensionClient) {
        queue.async { self.sinkClient = client }
    }

    func startSink() {
        queue.async { self.consumeNextFrame() }
    }

    func stopSink() {
        queue.async {
            self.sinkClient = nil
            self.latestFrame = nil
        }
    }

    private func consumeNextFrame() {
        guard let client = sinkClient else { return }
        sinkStream.stream.consumeSampleBuffer(from: client) { [weak self] sampleBuffer, _, _, _, error in
            guard let self else { return }
            self.queue.async {
                if let sampleBuffer, let image = CMSampleBufferGetImageBuffer(sampleBuffer) {
                    self.latestFrame = image
                    self.latestFrameTime = ProcessInfo.processInfo.systemUptime
                } else if let error {
                    log.debug("No frame from LumaLink Studio: \(error.localizedDescription)")
                }
                guard self.sinkClient != nil else { return }
                // Ask for the next one; when the queue was empty, wait a little.
                if sampleBuffer != nil {
                    self.consumeNextFrame()
                } else {
                    self.queue.asyncAfter(deadline: .now() + .milliseconds(5)) { self.consumeNextFrame() }
                }
            }
        }
    }
}

final class StreamSource: NSObject, CMIOExtensionStreamSource {
    private(set) var stream: CMIOExtensionStream!
    private let direction: CMIOExtensionStream.Direction
    private let format: CMIOExtensionStreamFormat
    private unowned let device: DeviceSource

    init(
        localizedName: String,
        streamID: UUID,
        direction: CMIOExtensionStream.Direction,
        format: CMIOExtensionStreamFormat,
        device: DeviceSource
    ) {
        self.direction = direction
        self.format = format
        self.device = device
        super.init()
        stream = CMIOExtensionStream(
            localizedName: localizedName,
            streamID: streamID,
            direction: direction,
            clockType: .hostTime,
            source: self
        )
    }

    var formats: [CMIOExtensionStreamFormat] { [format] }

    var availableProperties: Set<CMIOExtensionProperty> {
        var properties: Set<CMIOExtensionProperty> = [.streamActiveFormatIndex, .streamFrameDuration]
        if direction == .sink {
            properties.formUnion([.streamSinkBufferQueueSize, .streamSinkBuffersRequiredForStartup])
        }
        return properties
    }

    func streamProperties(forProperties properties: Set<CMIOExtensionProperty>) throws -> CMIOExtensionStreamProperties {
        let streamProperties = CMIOExtensionStreamProperties(dictionary: [:])
        if properties.contains(.streamActiveFormatIndex) {
            streamProperties.activeFormatIndex = 0
        }
        if properties.contains(.streamFrameDuration) {
            streamProperties.frameDuration = CMTime(value: 1, timescale: CameraConfig.frameRate)
        }
        // A short queue keeps the latency low: only the newest frames matter.
        if properties.contains(.streamSinkBufferQueueSize) {
            streamProperties.sinkBufferQueueSize = 2
        }
        if properties.contains(.streamSinkBuffersRequiredForStartup) {
            streamProperties.sinkBuffersRequiredForStartup = 1
        }
        return streamProperties
    }

    func setStreamProperties(_ streamProperties: CMIOExtensionStreamProperties) throws {}

    func authorizedToStartStream(for client: CMIOExtensionClient) -> Bool {
        if direction == .sink {
            device.authorizeSink(for: client)
        }
        return true
    }

    func startStream() throws {
        direction == .source ? device.startSource() : device.startSink()
    }

    func stopStream() throws {
        direction == .source ? device.stopSource() : device.stopSink()
    }
}

final class ProviderSource: NSObject, CMIOExtensionProviderSource {
    private(set) var provider: CMIOExtensionProvider!
    private var deviceSource: DeviceSource!

    init(clientQueue: DispatchQueue?) {
        super.init()
        provider = CMIOExtensionProvider(source: self, clientQueue: clientQueue)
        deviceSource = DeviceSource(localizedName: CameraConfig.name)
        do {
            try provider.addDevice(deviceSource.device)
        } catch {
            fatalError("Failed to add the device: \(error.localizedDescription)")
        }
    }

    func connect(to client: CMIOExtensionClient) throws {}

    func disconnect(from client: CMIOExtensionClient) {}

    var availableProperties: Set<CMIOExtensionProperty> { [.providerManufacturer] }

    func providerProperties(forProperties properties: Set<CMIOExtensionProperty>) throws -> CMIOExtensionProviderProperties {
        let providerProperties = CMIOExtensionProviderProperties(dictionary: [:])
        if properties.contains(.providerManufacturer) {
            providerProperties.manufacturer = CameraConfig.manufacturer
        }
        return providerProperties
    }

    func setProviderProperties(_ providerProperties: CMIOExtensionProviderProperties) throws {}
}
