import AppKit
import SwiftUI

/// `Friday --snapshot <fixtures> <folder>` draws the main screens from fixture data into PNG files, light and dark.
/// Each screen shows on the display for a moment, because lists draw only in a visible window. The CSV import
/// ticket is shown as running, which needs a live cw process in real use.
@MainActor
enum Snapshotter {
    static let flag = "--snapshot"
    nonisolated private static let size = NSSize(width: 1440, height: 920)
    private static let settleSeconds = 1.5

    private struct Scene {
        var name: String
        var sidebar: SidebarItem
        var ticket: String?
        var tab: TicketTab = .overview
        var agent: String?
    }

    static func render(fixtures: URL, output: URL) async {
        try? FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let project = "expense-tracker"
        let runningTicket = "261118-csv-import-a1b2"
        let scenes = [
            Scene(name: "1-project-home", sidebar: .project(project)),
            Scene(name: "2-running-ticket", sidebar: .project(project), ticket: "261118-csv-import-a1b2", tab: .activity),
            Scene(name: "3-agent-inspector", sidebar: .project(project), ticket: "261118-csv-import-a1b2", tab: .plan, agent: "W1"),
            Scene(name: "4-review", sidebar: .project(project), ticket: "261118-monthly-report-c3d4", tab: .review),
            Scene(name: "5-approve-plan", sidebar: .project(project), ticket: "261118-budget-alerts-e5f6", tab: .plan),
            Scene(name: "6-cost", sidebar: .project(project), ticket: "261118-csv-import-a1b2", tab: .cost),
            Scene(name: "7-blocked", sidebar: .project(project), ticket: "261118-cookie-auth-i9j0"),
            Scene(name: "8-usage", sidebar: .usage),
        ]
        for appearance in [NSAppearance.Name.aqua, .darkAqua] {
            NSApp.appearance = NSAppearance(named: appearance)
            for scene in scenes {
                let store = Store(fixtures: fixtures)
                await store.refreshAll()
                await store.refreshDoctor()
                if let index = store.tickets.firstIndex(where: { $0.id == runningTicket }) { store.tickets[index].running = true }
                store.sidebar = scene.sidebar
                store.selectedTicket = scene.ticket
                store.ticketTab = scene.tab
                store.showAgent = scene.agent
                let suffix = appearance == .aqua ? "light" : "dark"
                await capture(RootView().environment(store).environment(\.isSnapshot, true).tint(.blue), appearance: appearance, to: output.appendingPathComponent("\(scene.name)-\(suffix).png"))
            }
            let store = Store(fixtures: fixtures)
            await store.refreshAll()
            let sheet = NewTicketSheet(project: project).environment(store).tint(.blue)
            await capture(sheet, appearance: appearance, size: NSSize(width: 560, height: 300), to: output.appendingPathComponent("9-new-ticket-\(appearance == .aqua ? "light" : "dark").png"))
        }
    }

    private static func capture<V: View>(_ view: V, appearance: NSAppearance.Name, size: NSSize = size, to file: URL) async {
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: [.titled, .fullSizeContentView], backing: .buffered, defer: false)
        window.appearance = NSAppearance(named: appearance)
        let host = NSHostingView(rootView: view.frame(width: size.width, height: size.height))
        window.contentView = host
        window.setFrameOrigin(.zero)
        window.orderFrontRegardless()
        try? await Task.sleep(nanoseconds: UInt64(settleSeconds * 1_000_000_000))
        host.layoutSubtreeIfNeeded()
        guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return }
        host.cacheDisplay(in: host.bounds, to: bitmap)
        try? bitmap.representation(using: .png, properties: [:])?.write(to: file)
        window.orderOut(nil)
    }
}
