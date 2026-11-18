import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        let arguments = CommandLine.arguments
        if let flag = arguments.firstIndex(of: Snapshotter.flag), arguments.count > flag + 2 {
            Task { @MainActor in
                await Snapshotter.render(fixtures: URL(fileURLWithPath: arguments[flag + 1]), output: URL(fileURLWithPath: arguments[flag + 2]))
                NSApp.terminate(nil)
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct FridayApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var store = Store()
    @AppStorage(Preference.accent) private var accent = Preference.defaultAccent

    var body: some Scene {
        WindowGroup("FRIDAY") {
            if !CommandLine.arguments.contains(Snapshotter.flag) {
                RootView()
                    .environment(store)
                    .frame(minWidth: 1100, minHeight: 720)
                    .tint(Theme.accents[accent] ?? .blue)
                    .task { store.start() }
            }
        }
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New ticket") { store.sheet = .newTicket(project: store.selectedProject) }.keyboardShortcut("n")
                Button("New project from an idea") { store.sheet = .newProject }.keyboardShortcut("n", modifiers: [.command, .shift])
                Button("Add a repository") { store.sheet = .addProject }
            }
            CommandGroup(after: .toolbar) {
                Button("Refresh") { Task { await store.refreshAll() } }.keyboardShortcut("r")
            }
        }
    }
}
