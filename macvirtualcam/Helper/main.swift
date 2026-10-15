// lumalink-camera: LumaLink Studio's helper for the LumaLink Camera extension.
//
//   lumalink-camera install     ask macOS to install (activate) the extension
//   lumalink-camera uninstall   remove it
//   lumalink-camera status      exit 0 when the camera is present, 1 when not
//   lumalink-camera list        print the names of all cameras
//   lumalink-camera feed        send RGB frames from stdin to the camera
//
// It lives in LumaLink Studio.app/Contents/MacOS, so macOS treats it as part
// of the app that carries the extension in Contents/Library/SystemExtensions:
// only that app may install it. `feed` reads raw frames of
// CameraConfig.width × height × 3 bytes, RGB, top row first, until stdin ends.

import Accelerate
import CoreMediaIO
import CoreVideo
import Foundation
import SystemExtensions

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

// MARK: Install and uninstall

final class ExtensionRequest: NSObject, OSSystemExtensionRequestDelegate {
    func run(_ request: OSSystemExtensionRequest) -> Never {
        request.delegate = self
        OSSystemExtensionManager.shared.submitRequest(request)
        dispatchMain()
    }

    func request(
        _ request: OSSystemExtensionRequest,
        actionForReplacingExtension existing: OSSystemExtensionProperties,
        withExtension ext: OSSystemExtensionProperties
    ) -> OSSystemExtensionRequest.ReplacementAction {
        .replace
    }

    func requestNeedsUserApproval(_ request: OSSystemExtensionRequest) {
        // LumaLink Studio shows this line to the user.
        print("approval: Allow LumaLink Camera in System Settings → General → Login Items & Extensions → Camera Extensions")
        fflush(stdout)
    }

    func request(_ request: OSSystemExtensionRequest, didFinishWithResult result: OSSystemExtensionRequest.Result) {
        print(result == .willCompleteAfterReboot ? "done: after a restart" : "done")
        exit(0)
    }

    func request(_ request: OSSystemExtensionRequest, didFailWithError error: Error) {
        fail("error: \(error.localizedDescription)")
    }
}

// MARK: Finding the camera

/// A property made of UInt32 values: object IDs, or a stream's direction.
func uint32Property(_ object: CMIOObjectID, _ selector: Int) -> [UInt32] {
    var address = CMIOObjectPropertyAddress(
        mSelector: CMIOObjectPropertySelector(selector),
        mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
        mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain)
    )
    var size: UInt32 = 0
    guard CMIOObjectGetPropertyDataSize(object, &address, 0, nil, &size) == noErr, size > 0 else {
        return []
    }
    var values = [UInt32](repeating: 0, count: Int(size) / MemoryLayout<UInt32>.stride)
    var used: UInt32 = 0
    guard CMIOObjectGetPropertyData(object, &address, 0, nil, size, &used, &values) == noErr else {
        return []
    }
    return Array(values.prefix(Int(used) / MemoryLayout<UInt32>.stride))
}

func name(of object: CMIOObjectID) -> String? {
    var address = CMIOObjectPropertyAddress(
        mSelector: CMIOObjectPropertySelector(kCMIOObjectPropertyName),
        mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
        mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain)
    )
    var value: Unmanaged<CFString>?
    var used: UInt32 = 0
    let size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard CMIOObjectGetPropertyData(object, &address, 0, nil, size, &used, &value) == noErr else {
        return nil
    }
    return value?.takeRetainedValue() as String?
}

/// Every camera the system offers, camera extensions included.
func allDevices() -> [CMIODeviceID] {
    // Lets this process see camera extensions, not only built-in cameras.
    var allow: UInt32 = 1
    var address = CMIOObjectPropertyAddress(
        mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyAllowScreenCaptureDevices),
        mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
        mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain)
    )
    CMIOObjectSetPropertyData(CMIOObjectID(kCMIOObjectSystemObject), &address, 0, nil, UInt32(MemoryLayout<UInt32>.size), &allow)

    return uint32Property(CMIOObjectID(kCMIOObjectSystemObject), kCMIOHardwarePropertyDevices)
}

/// The LumaLink Camera device and its sink stream, the one that takes frames.
func findCamera() -> (device: CMIODeviceID, sink: CMIOStreamID)? {
    for device in allDevices() where name(of: device) == CameraConfig.name {
        let streams = uint32Property(device, kCMIODevicePropertyStreams)
        // 0 is an output stream: the client fills its queue.
        if let sink = streams.first(where: { uint32Property($0, kCMIOStreamPropertyDirection).first == 0 }) {
            return (device, sink)
        }
    }
    return nil
}

// MARK: Feeding frames

func feed() -> Never {
    guard let camera = findCamera() else {
        fail("LumaLink Camera is not installed")
    }
    var unmanagedQueue: Unmanaged<CMSimpleQueue>?
    guard CMIOStreamCopyBufferQueue(camera.sink, { _, _, _ in }, nil, &unmanagedQueue) == noErr,
          let queue = unmanagedQueue?.takeRetainedValue()
    else {
        fail("Could not open the LumaLink Camera input")
    }
    guard CMIODeviceStartStream(camera.device, camera.sink) == noErr else {
        fail("Could not start the LumaLink Camera input")
    }
    defer { CMIODeviceStopStream(camera.device, camera.sink) }

    let width = Int(CameraConfig.width)
    let height = Int(CameraConfig.height)
    var format: CMFormatDescription?
    CMVideoFormatDescriptionCreate(
        allocator: kCFAllocatorDefault,
        codecType: kCVPixelFormatType_32BGRA,
        width: CameraConfig.width,
        height: CameraConfig.height,
        extensions: nil,
        formatDescriptionOut: &format
    )
    var pool: CVPixelBufferPool?
    let attributes: [CFString: Any] = [
        kCVPixelBufferWidthKey: width,
        kCVPixelBufferHeightKey: height,
        kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA,
        kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary,
    ]
    CVPixelBufferPoolCreate(kCFAllocatorDefault, nil, attributes as CFDictionary, &pool)
    guard let format, let pool else { fail("Could not prepare the frame buffers") }

    let frameSize = width * height * 3
    var frame = [UInt8](repeating: 0, count: frameSize)
    let input = FileHandle.standardInput
    while true {
        // Read one whole frame, or stop when LumaLink Studio closes the pipe.
        var filled = 0
        while filled < frameSize {
            let chunk = input.readData(ofLength: frameSize - filled)
            if chunk.isEmpty { exit(0) }
            _ = frame.withUnsafeMutableBytes { chunk.copyBytes(to: UnsafeMutableRawBufferPointer(rebasing: $0[filled...])) }
            filled += chunk.count
        }

        // Only the newest frames matter: drop this one rather than queue it.
        if CMSimpleQueueGetCount(queue) >= CMSimpleQueueGetCapacity(queue) {
            continue
        }
        var pixelBuffer: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &pixelBuffer) == kCVReturnSuccess,
              let pixelBuffer
        else { continue }
        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        frame.withUnsafeMutableBytes { source in
            var rgb = vImage_Buffer(data: source.baseAddress, height: vImagePixelCount(height), width: vImagePixelCount(width), rowBytes: width * 3)
            var bgra = vImage_Buffer(
                data: CVPixelBufferGetBaseAddress(pixelBuffer),
                height: vImagePixelCount(height),
                width: vImagePixelCount(width),
                rowBytes: CVPixelBufferGetBytesPerRow(pixelBuffer)
            )
            vImageConvert_RGB888toBGRA8888(&rgb, nil, 255, &bgra, false, vImage_Flags(kvImageNoFlags))
        }
        CVPixelBufferUnlockBaseAddress(pixelBuffer, [])

        var timing = CMSampleTimingInfo(
            duration: CMTime(value: 1, timescale: CameraConfig.frameRate),
            presentationTimeStamp: CMClockGetTime(CMClockGetHostTimeClock()),
            decodeTimeStamp: .invalid
        )
        var sampleBuffer: CMSampleBuffer?
        CMSampleBufferCreateForImageBuffer(
            allocator: kCFAllocatorDefault,
            imageBuffer: pixelBuffer,
            dataReady: true,
            makeDataReadyCallback: nil,
            refcon: nil,
            formatDescription: format,
            sampleTiming: &timing,
            sampleBufferOut: &sampleBuffer
        )
        if let sampleBuffer {
            // The queue owns the buffer; the extension releases it.
            CMSimpleQueueEnqueue(queue, element: Unmanaged.passRetained(sampleBuffer).toOpaque())
        }
    }
}

// MARK: Main

// The request holds its delegate weakly; this keeps it for the process's life.
let extensionRequest = ExtensionRequest()

switch CommandLine.arguments.dropFirst().first {
case "install":
    extensionRequest.run(.activationRequest(forExtensionWithIdentifier: CameraConfig.extensionIdentifier, queue: .main))
case "uninstall":
    extensionRequest.run(.deactivationRequest(forExtensionWithIdentifier: CameraConfig.extensionIdentifier, queue: .main))
case "status":
    exit(findCamera() == nil ? 1 : 0)
case "list":
    for device in allDevices() {
        print(name(of: device) ?? "?")
    }
case "feed":
    feed()
default:
    fail("usage: lumalink-camera install | uninstall | status | list | feed", code: 2)
}
