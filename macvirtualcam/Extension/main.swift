// LumaLink Camera: the macOS camera extension other applications see.

import CoreMediaIO
import Foundation

let providerSource = ProviderSource(clientQueue: nil)
CMIOExtensionProvider.startService(provider: providerSource.provider)

CFRunLoopRun()
