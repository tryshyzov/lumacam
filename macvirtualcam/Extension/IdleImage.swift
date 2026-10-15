// The picture shown while LumaLink Studio sends no video: a camera glyph on
// gray, the same as idle_frame() in camera_studio/virtual_camera.py and
// FillIdleImage in the Windows filter.

import CoreGraphics
import CoreVideo

enum IdleImage {
    static let background: CGFloat = 128 / 255
    static let glyph: CGFloat = 205 / 255

    static func make(width: Int, height: Int) -> CVPixelBuffer {
        var buffer: CVPixelBuffer?
        let attributes: [CFString: Any] = [kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary]
        CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA, attributes as CFDictionary, &buffer)
        let image = buffer!
        CVPixelBufferLockBaseAddress(image, [])
        defer { CVPixelBufferUnlockBaseAddress(image, []) }

        let context = CGContext(
            data: CVPixelBufferGetBaseAddress(image),
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: CVPixelBufferGetBytesPerRow(image),
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue
        )!
        context.setFillColor(gray: background, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))

        // Sizes are fractions of the height, as in idle_frame(); the glyph is
        // symmetric, so Core Graphics' upward y axis changes nothing.
        let unit = CGFloat(height)
        let x = CGFloat(width) / 2
        let y = CGFloat(height) / 2
        context.setFillColor(gray: glyph, alpha: 1)
        context.beginPath()
        context.move(to: CGPoint(x: x - 0.131 * unit, y: y - 0.084 * unit))
        context.addLine(to: CGPoint(x: x - 0.070 * unit, y: y - 0.045 * unit))
        context.addLine(to: CGPoint(x: x - 0.070 * unit, y: y + 0.045 * unit))
        context.addLine(to: CGPoint(x: x - 0.131 * unit, y: y + 0.084 * unit))
        context.closePath()
        context.fillPath()
        let body = CGRect(x: x - 0.050 * unit, y: y - 0.0985 * unit, width: 0.181 * unit, height: 0.197 * unit)
        context.addPath(CGPath(roundedRect: body, cornerWidth: 0.033 * unit, cornerHeight: 0.033 * unit, transform: nil))
        context.fillPath()
        return image
    }
}
