#import <AppKit/AppKit.h>

static NSData *MIIconData(NSInteger size) {
    NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc]
        initWithBitmapDataPlanes:NULL
        pixelsWide:size
        pixelsHigh:size
        bitsPerSample:8
        samplesPerPixel:4
        hasAlpha:YES
        isPlanar:NO
        colorSpaceName:NSDeviceRGBColorSpace
        bytesPerRow:0
        bitsPerPixel:0];
    if (!bitmap) return nil;

    [NSGraphicsContext saveGraphicsState];
    NSGraphicsContext.currentContext = [NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap];
    CGFloat s = (CGFloat)size;
    NSRect bounds = NSMakeRect(0, 0, s, s);
    [NSColor.clearColor setFill];
    NSRectFill(bounds);

    CGFloat inset = s * 0.055;
    NSBezierPath *tile = [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(bounds, inset, inset) xRadius:s * 0.215 yRadius:s * 0.215];
    NSGradient *gradient = [[NSGradient alloc] initWithColors:@[
        [NSColor colorWithCalibratedRed:0.035 green:0.043 blue:0.055 alpha:1],
        [NSColor colorWithCalibratedRed:0.105 green:0.125 blue:0.155 alpha:1]
    ]];
    [gradient drawInBezierPath:tile angle:-70];

    NSBezierPath *accent = [NSBezierPath bezierPath];
    [accent moveToPoint:NSMakePoint(s * 0.18, s * 0.28)];
    [accent lineToPoint:NSMakePoint(s * 0.36, s * 0.47)];
    [accent lineToPoint:NSMakePoint(s * 0.53, s * 0.39)];
    [accent lineToPoint:NSMakePoint(s * 0.76, s * 0.70)];
    accent.lineWidth = MAX(2, s * 0.055);
    accent.lineCapStyle = NSLineCapStyleRound;
    accent.lineJoinStyle = NSLineJoinStyleRound;
    [[NSColor colorWithCalibratedRed:0.96 green:0.54 blue:0.08 alpha:1] setStroke];
    [accent stroke];

    CGFloat dotRadius = s * 0.038;
    NSArray<NSValue *> *points = @[
        [NSValue valueWithPoint:NSMakePoint(s * 0.36, s * 0.47)],
        [NSValue valueWithPoint:NSMakePoint(s * 0.53, s * 0.39)],
        [NSValue valueWithPoint:NSMakePoint(s * 0.76, s * 0.70)]
    ];
    [NSColor.whiteColor setFill];
    for (NSValue *value in points) {
        NSPoint point = value.pointValue;
        [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(point.x - dotRadius, point.y - dotRadius, dotRadius * 2, dotRadius * 2)] fill];
    }

    NSMutableParagraphStyle *paragraph = [[NSMutableParagraphStyle alloc] init];
    paragraph.alignment = NSTextAlignmentLeft;
    NSDictionary *attributes = @{
        NSFontAttributeName: [NSFont monospacedSystemFontOfSize:s * 0.19 weight:NSFontWeightBold],
        NSForegroundColorAttributeName: NSColor.whiteColor,
        NSParagraphStyleAttributeName: paragraph
    };
    [[[NSAttributedString alloc] initWithString:@"MI" attributes:attributes]
        drawInRect:NSMakeRect(s * 0.17, s * 0.69, s * 0.45, s * 0.22)];

    [NSGraphicsContext restoreGraphicsState];
    return [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) {
            fprintf(stderr, "usage: AppIconGenerator <iconset-dir>\n");
            return 2;
        }
        NSString *output = [NSString stringWithUTF8String:argv[1]];
        [[NSFileManager defaultManager] createDirectoryAtPath:output withIntermediateDirectories:YES attributes:nil error:nil];
        NSArray<NSArray *> *variants = @[
            @[@"icon_16x16.png", @16], @[@"icon_16x16@2x.png", @32],
            @[@"icon_32x32.png", @32], @[@"icon_32x32@2x.png", @64],
            @[@"icon_128x128.png", @128], @[@"icon_128x128@2x.png", @256],
            @[@"icon_256x256.png", @256], @[@"icon_256x256@2x.png", @512],
            @[@"icon_512x512.png", @512], @[@"icon_512x512@2x.png", @1024]
        ];
        for (NSArray *variant in variants) {
            NSData *data = MIIconData([variant[1] integerValue]);
            if (!data || ![data writeToFile:[output stringByAppendingPathComponent:variant[0]] atomically:YES]) return 1;
        }
    }
    return 0;
}
