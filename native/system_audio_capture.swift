import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

@available(macOS 13.0, *)
final class SystemAudioSink: NSObject, SCStreamOutput {
    private let output = FileHandle.standardOutput

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard outputType == .audio, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        guard let description = CMSampleBufferGetFormatDescription(sampleBuffer),
              let basicPointer = CMAudioFormatDescriptionGetStreamBasicDescription(description) else { return }
        let basic = basicPointer.pointee
        guard basic.mFormatID == kAudioFormatLinearPCM,
              basic.mBitsPerChannel == 32,
              (basic.mFormatFlags & kAudioFormatFlagIsFloat) != 0 else { return }

        var requiredSize = 0
        var retainedBlock: CMBlockBuffer?
        let sizeStatus = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: &requiredSize,
            bufferListOut: nil,
            bufferListSize: 0,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: 0,
            blockBufferOut: &retainedBlock
        )
        guard sizeStatus == noErr, requiredSize > 0 else { return }

        let storage = UnsafeMutableRawPointer.allocate(
            byteCount: requiredSize,
            alignment: MemoryLayout<AudioBufferList>.alignment
        )
        defer { storage.deallocate() }
        let audioBufferList = storage.bindMemory(to: AudioBufferList.self, capacity: 1)
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: audioBufferList,
            bufferListSize: requiredSize,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: UInt32(kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment),
            blockBufferOut: &retainedBlock
        )
        guard status == noErr else { return }

        let buffers = UnsafeMutableAudioBufferListPointer(audioBufferList)
        let frameCount = CMSampleBufferGetNumSamples(sampleBuffer)
        guard frameCount > 0, !buffers.isEmpty else { return }
        var mono = [Float](repeating: 0, count: frameCount)
        let nonInterleaved = (basic.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0

        if nonInterleaved || buffers.count > 1 {
            var contributingChannels = 0
            for buffer in buffers {
                guard let data = buffer.mData else { continue }
                let pointer = data.assumingMemoryBound(to: Float.self)
                let channels = max(1, Int(buffer.mNumberChannels))
                for frame in 0..<frameCount {
                    var sum: Float = 0
                    for channel in 0..<channels { sum += pointer[frame * channels + channel] }
                    mono[frame] += sum / Float(channels)
                }
                contributingChannels += 1
            }
            if contributingChannels > 1 {
                let scale = Float(contributingChannels)
                for index in mono.indices { mono[index] /= scale }
            }
        } else {
            let buffer = buffers[0]
            guard let data = buffer.mData else { return }
            let pointer = data.assumingMemoryBound(to: Float.self)
            let channels = max(1, Int(basic.mChannelsPerFrame))
            for frame in 0..<frameCount {
                var sum: Float = 0
                for channel in 0..<channels { sum += pointer[frame * channels + channel] }
                mono[frame] = sum / Float(channels)
            }
        }

        mono.withUnsafeBytes { bytes in
            output.write(Data(bytes))
        }
    }
}

@main
struct WordGrabSystemAudioCapture {
    static func report(_ message: String) {
        FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    }

    static func main() async {
        guard #available(macOS 13.0, *) else {
            report("ERROR 系统音频录制需要 macOS 13 或更高版本")
            exit(2)
        }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                false,
                onScreenWindowsOnly: false
            )
            guard let display = content.displays.first else {
                report("ERROR 没有检测到可用于系统音频录制的显示器")
                exit(3)
            }

            let filter = SCContentFilter(display: display, excludingWindows: [])
            let configuration = SCStreamConfiguration()
            configuration.capturesAudio = true
            configuration.excludesCurrentProcessAudio = true
            configuration.sampleRate = 48_000
            configuration.channelCount = 2
            configuration.width = 2
            configuration.height = 2
            configuration.showsCursor = false
            configuration.queueDepth = 3
            configuration.minimumFrameInterval = CMTime(value: 1, timescale: 2)

            let sink = SystemAudioSink()
            let stream = SCStream(filter: filter, configuration: configuration, delegate: nil)
            let queue = DispatchQueue(label: "com.local.wordgrab.system-audio")
            try stream.addStreamOutput(sink, type: .audio, sampleHandlerQueue: queue)
            try await stream.startCapture()
            report("READY 48000 1")

            // The parent keeps stdin open for the lifetime of the capture.
            // Closing it is a graceful, deterministic stop signal.
            _ = FileHandle.standardInput.readDataToEndOfFile()
            try await stream.stopCapture()
        } catch {
            report("ERROR \(error.localizedDescription)")
            exit(1)
        }
    }
}
