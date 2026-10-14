#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <unistd.h>

static NSString * const MIDashboardURL = @"http://127.0.0.1:8787/";
static NSString * const MIStatusURL = @"http://127.0.0.1:8787/api/status";
// Project root: $MARKET_INTEL_HOME if set, otherwise the checkout that contains dist/Market Intel.app.
static NSString *MIProjectRoot(void) {
    NSString *env = NSProcessInfo.processInfo.environment[@"MARKET_INTEL_HOME"];
    if (env.length > 0) return env;
    return [[[NSBundle mainBundle].bundlePath stringByDeletingLastPathComponent] stringByDeletingLastPathComponent];
}

@interface MIAppDelegate : NSObject <NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) NSTextField *statusLabel;
@property(nonatomic, strong) NSButton *backButton;
@property(nonatomic, strong) NSButton *forwardButton;
@property(nonatomic, strong) NSTask *standaloneServer;
@property(nonatomic, assign) BOOL serviceStartRequested;
@end

@implementation MIAppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [self buildWindow];
    [self showLoading:@"Connecting to the local Market Intel service…"];
    [self checkServiceAtAttempt:0];
    [NSApp activateIgnoringOtherApps:YES];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return YES;
}

- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)flag {
    if (!flag) {
        [self.window makeKeyAndOrderFront:nil];
    }
    return YES;
}

- (void)buildWindow {
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = WKWebsiteDataStore.defaultDataStore;
    configuration.preferences.javaScriptCanOpenWindowsAutomatically = NO;

    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.webView.navigationDelegate = self;
    self.webView.UIDelegate = self;
    self.webView.allowsMagnification = YES;

    NSVisualEffectView *toolbar = [[NSVisualEffectView alloc] initWithFrame:NSZeroRect];
    toolbar.material = NSVisualEffectMaterialHeaderView;
    toolbar.blendingMode = NSVisualEffectBlendingModeWithinWindow;
    toolbar.state = NSVisualEffectStateActive;
    toolbar.translatesAutoresizingMaskIntoConstraints = NO;

    self.backButton = [self buttonWithSymbol:@"chevron.left" help:@"Back" action:@selector(goBack:)];
    self.forwardButton = [self buttonWithSymbol:@"chevron.right" help:@"Forward" action:@selector(goForward:)];
    NSButton *homeButton = [self buttonWithSymbol:@"house" help:@"Home" action:@selector(goHome:)];
    NSButton *reloadButton = [self buttonWithSymbol:@"arrow.clockwise" help:@"Reload" action:@selector(reloadPage:)];
    NSButton *browserButton = [self buttonWithSymbol:@"safari" help:@"Open in browser" action:@selector(openInBrowser:)];

    self.statusLabel = [NSTextField labelWithString:@"Local · 127.0.0.1:8787"];
    self.statusLabel.font = [NSFont systemFontOfSize:12 weight:NSFontWeightMedium];
    self.statusLabel.textColor = NSColor.secondaryLabelColor;
    self.statusLabel.lineBreakMode = NSLineBreakByTruncatingMiddle;
    [self.statusLabel setContentHuggingPriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    [self.statusLabel setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];

    NSStackView *stack = [NSStackView stackViewWithViews:@[self.backButton, self.forwardButton, homeButton, reloadButton, self.statusLabel, browserButton]];
    stack.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    stack.alignment = NSLayoutAttributeCenterY;
    stack.spacing = 8;
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    [toolbar addSubview:stack];

    NSView *content = [[NSView alloc] initWithFrame:NSZeroRect];
    [content addSubview:toolbar];
    [content addSubview:self.webView];
    self.webView.translatesAutoresizingMaskIntoConstraints = NO;

    [NSLayoutConstraint activateConstraints:@[
        [toolbar.topAnchor constraintEqualToAnchor:content.topAnchor],
        [toolbar.leadingAnchor constraintEqualToAnchor:content.leadingAnchor],
        [toolbar.trailingAnchor constraintEqualToAnchor:content.trailingAnchor],
        [toolbar.heightAnchor constraintEqualToConstant:46],
        [stack.leadingAnchor constraintEqualToAnchor:toolbar.leadingAnchor constant:12],
        [stack.trailingAnchor constraintEqualToAnchor:toolbar.trailingAnchor constant:-12],
        [stack.centerYAnchor constraintEqualToAnchor:toolbar.centerYAnchor],
        [self.webView.topAnchor constraintEqualToAnchor:toolbar.bottomAnchor],
        [self.webView.leadingAnchor constraintEqualToAnchor:content.leadingAnchor],
        [self.webView.trailingAnchor constraintEqualToAnchor:content.trailingAnchor],
        [self.webView.bottomAnchor constraintEqualToAnchor:content.bottomAnchor]
    ]];

    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1440, 900)
                                              styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
    self.window.title = @"Market Intel";
    self.window.minSize = NSMakeSize(960, 640);
    self.window.contentView = content;
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [self updateNavigationButtons];
}

- (NSButton *)buttonWithSymbol:(NSString *)symbol help:(NSString *)help action:(SEL)action {
    NSImage *image = [NSImage imageWithSystemSymbolName:symbol accessibilityDescription:help];
    NSButton *button = [NSButton buttonWithImage:image target:self action:action];
    button.bezelStyle = NSBezelStyleTexturedRounded;
    button.toolTip = help;
    button.imagePosition = NSImageOnly;
    button.accessibilityLabel = help;
    return button;
}

- (void)checkServiceAtAttempt:(NSInteger)attempt {
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:MIStatusURL]];
    request.timeoutInterval = 1.5;
    __weak typeof(self) weakSelf = self;
    [[[NSURLSession sharedSession] dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        BOOL ok = [(NSHTTPURLResponse *)response statusCode] == 200;
        dispatch_async(dispatch_get_main_queue(), ^{
            typeof(self) self = weakSelf;
            if (!self) return;
            if (ok) {
                self.statusLabel.stringValue = @"Local · connected";
                [self.webView loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:MIDashboardURL]]];
                return;
            }
            if (!self.serviceStartRequested) {
                self.serviceStartRequested = YES;
                self.statusLabel.stringValue = @"Starting local service…";
                [self startService];
            }
            if (attempt < 60) {
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
                    [self checkServiceAtAttempt:attempt + 1];
                });
            } else {
                self.statusLabel.stringValue = @"Local service unavailable";
                [self showFailure];
            }
        });
    }] resume];
}

- (void)startService {
    NSTask *launchctl = [[NSTask alloc] init];
    launchctl.executableURL = [NSURL fileURLWithPath:@"/bin/launchctl"];
    launchctl.arguments = @[@"kickstart", @"-k", [NSString stringWithFormat:@"gui/%d/com.siyanchen.marketintel", getuid()]];
    launchctl.standardOutput = NSFileHandle.fileHandleWithNullDevice;
    launchctl.standardError = NSFileHandle.fileHandleWithNullDevice;
    __weak typeof(self) weakSelf = self;
    launchctl.terminationHandler = ^(NSTask *task) {
        if (task.terminationStatus != 0) {
            [weakSelf startStandaloneService];
        }
    };
    NSError *error = nil;
    if (![launchctl launchAndReturnError:&error]) {
        [self startStandaloneService];
    }
}

- (void)startStandaloneService {
    if (self.standaloneServer) return;
    NSString *preferredPython = @"/opt/anaconda3/bin/python3";
    NSString *python = [[NSFileManager defaultManager] isExecutableFileAtPath:preferredPython] ? preferredPython : @"/usr/bin/python3";
    NSString *logs = [MIProjectRoot() stringByAppendingPathComponent:@"outputs/logs"];
    [[NSFileManager defaultManager] createDirectoryAtPath:logs withIntermediateDirectories:YES attributes:nil error:nil];
    NSString *logPath = [logs stringByAppendingPathComponent:@"market_intel_desktop.out"];
    if (![[NSFileManager defaultManager] fileExistsAtPath:logPath]) {
        [[NSFileManager defaultManager] createFileAtPath:logPath contents:nil attributes:nil];
    }

    NSTask *process = [[NSTask alloc] init];
    process.executableURL = [NSURL fileURLWithPath:python];
    process.arguments = @[@"-m", @"market_intel", @"serve"];
    process.currentDirectoryURL = [NSURL fileURLWithPath:MIProjectRoot() isDirectory:YES];
    NSMutableDictionary *environment = [NSProcessInfo.processInfo.environment mutableCopy];
    environment[@"PATH"] = @"/opt/anaconda3/bin:/usr/local/bin:/usr/bin:/bin";
    environment[@"PYTHONUNBUFFERED"] = @"1";
    process.environment = environment;
    NSFileHandle *handle = [NSFileHandle fileHandleForWritingAtPath:logPath];
    [handle seekToEndOfFile];
    process.standardOutput = handle;
    process.standardError = handle;
    NSError *error = nil;
    if ([process launchAndReturnError:&error]) {
        self.standaloneServer = process;
    } else {
        dispatch_async(dispatch_get_main_queue(), ^{
            self.statusLabel.stringValue = @"Could not start local service";
        });
    }
}

- (void)showLoading:(NSString *)message {
    NSString *html = [NSString stringWithFormat:@"<!doctype html><meta charset='utf-8'><style>body{font:15px -apple-system,BlinkMacSystemFont,sans-serif;background:#f7f7f5;color:#222;display:grid;place-items:center;height:100vh;margin:0}.card{background:white;border:1px solid #ddd;border-radius:14px;padding:28px 34px;box-shadow:0 8px 30px #00000010}.dot{display:inline-block;width:9px;height:9px;border-radius:50%%;background:#d97706;margin-right:10px;animation:p 1s infinite alternate}@keyframes p{to{opacity:.25}}</style><div class='card'><span class='dot'></span>%@</div>", message];
    [self.webView loadHTMLString:html baseURL:nil];
}

- (void)showFailure {
    NSString *html = @"<!doctype html><meta charset='utf-8'><style>body{font:15px -apple-system,BlinkMacSystemFont,sans-serif;background:#f7f7f5;color:#222;display:grid;place-items:center;height:100vh;margin:0}.card{max-width:560px;background:white;border:1px solid #ddd;border-radius:14px;padding:28px 34px;box-shadow:0 8px 30px #00000010}h2{margin-top:0}code{background:#f1f1ef;padding:2px 5px;border-radius:4px}</style><div class='card'><h2>Market Intel could not start</h2><p>The app could not reach <code>127.0.0.1:8787</code>.</p><p>Check <code>outputs/logs/market_intel.out</code>, then use the reload button.</p></div>";
    [self.webView loadHTMLString:html baseURL:nil];
}

- (void)goBack:(id)sender {
    if (self.webView.canGoBack) [self.webView goBack];
}

- (void)goForward:(id)sender {
    if (self.webView.canGoForward) [self.webView goForward];
}

- (void)goHome:(id)sender {
    [self.webView loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:MIDashboardURL]]];
}

- (void)reloadPage:(id)sender {
    if ([self.webView.URL.host isEqualToString:@"127.0.0.1"]) {
        [self.webView reload];
    } else {
        self.serviceStartRequested = NO;
        [self showLoading:@"Reconnecting to the local Market Intel service…"];
        [self checkServiceAtAttempt:0];
    }
}

- (void)openInBrowser:(id)sender {
    [[NSWorkspace sharedWorkspace] openURL:self.webView.URL ?: [NSURL URLWithString:MIDashboardURL]];
}

- (void)updateNavigationButtons {
    self.backButton.enabled = self.webView.canGoBack;
    self.forwardButton.enabled = self.webView.canGoForward;
}

- (BOOL)isLocalURL:(NSURL *)url {
    return ([url.host isEqualToString:@"127.0.0.1"] || [url.host isEqualToString:@"localhost"]) && url.port.integerValue == 8787;
}

- (void)webView:(WKWebView *)webView didStartProvisionalNavigation:(WKNavigation *)navigation {
    [self updateNavigationButtons];
}

- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    [self updateNavigationButtons];
    self.window.title = webView.title.length ? [NSString stringWithFormat:@"Market Intel — %@", webView.title] : @"Market Intel";
    self.statusLabel.stringValue = [webView.URL.path isEqualToString:@"/"] ? @"Local · Home" : [NSString stringWithFormat:@"Local · %@", webView.URL.path ?: @"connected"];
}

- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)navigationAction decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    NSURL *url = navigationAction.request.URL;
    if (!url) {
        decisionHandler(WKNavigationActionPolicyCancel);
    } else if ([url.scheme isEqualToString:@"about"] || [url.scheme isEqualToString:@"data"] || [self isLocalURL:url]) {
        decisionHandler(WKNavigationActionPolicyAllow);
    } else {
        [[NSWorkspace sharedWorkspace] openURL:url];
        decisionHandler(WKNavigationActionPolicyCancel);
    }
}

- (WKWebView *)webView:(WKWebView *)webView createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration forNavigationAction:(WKNavigationAction *)navigationAction windowFeatures:(WKWindowFeatures *)windowFeatures {
    NSURL *url = navigationAction.request.URL;
    if ([self isLocalURL:url]) {
        [webView loadRequest:[NSURLRequest requestWithURL:url]];
    } else if (url) {
        [[NSWorkspace sharedWorkspace] openURL:url];
    }
    return nil;
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        MIAppDelegate *delegate = [[MIAppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
