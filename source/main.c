#include <nds.h>
#include <dswifi9.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>

// ---------- Screen / stream constants ----------
#define MAX_ITEMS        65536   // > 49152 worst‑case (div 1, cluster 1)

// Default network settings – editable on the DS
#define DEFAULT_IP       "192.168.1.100"
#define SERVER_PORT      8888

// Allowed resolution dividers (must divide both 256 and 192)
const int ALLOWED_DIVIDERS[] = {1, 2, 4, 8, 16, 32, 64};
#define NUM_DIVIDERS  (sizeof(ALLOWED_DIVIDERS) / sizeof(ALLOWED_DIVIDERS[0]))

// ---------- Globals ----------
u16* vramFront;                     // pointer to hardware VRAM
u16 palette[256];                   // R3G3B2 → RGB15

// Mutable settings
char serverIP[16] = DEFAULT_IP;
int  currentDivider = 4;            // used only for local test; stream auto‑detects

// Back‑buffer for double‑buffering (tear‑free display)
u16 backBuffer[256 * 192] __attribute__((aligned(4)));

// Manual resolution override from server (via 0xFFFF control packet)
int  manualDivider = 0;
bool useManualDivider = false;

// ---------- DMA copy from back‑buffer to VRAM during VBlank ----------
static void swapBuffers(void) {
    dmaCopyHalfWords(3, backBuffer, vramFront, 256 * 192 * sizeof(u16));
}

// ---------- Drawing helpers (write into the back‑buffer) ----------
static inline void drawBlock(int bx, int by, u16 color, int resDiv) {
    int px = bx * resDiv;
    int py = by * resDiv;
    for (int y = 0; y < resDiv; y++) {
        u16* row = backBuffer + (py + y) * 256 + px;
        for (int x = 0; x < resDiv; x++) {
            row[x] = color;
        }
    }
}

void decodeScratch(const u16* pixelColors, const u16* amountPerPixel,
                   int numItems, int resDiv, int logicW, int logicH) {
    int bx = 0, by = 0;
    for (int i = 0; i < numItems; i++) {
        u16 color = pixelColors[i];
        int blocks = amountPerPixel[i] / resDiv;
        for (int j = 0; j < blocks; j++) {
            drawBlock(bx, by, color, resDiv);
            bx++;
            if (bx >= logicW) {
                bx = 0;
                by++;
                if (by >= logicH) return;
            }
        }
    }
}

// ---------- Network helpers ----------
static int recvAll(int sock, void* buf, int len) {
    int received = 0;
    char* ptr = (char*)buf;
    while (received < len) {
        int r = recv(sock, ptr + received, len - received, 0);
        if (r <= 0) return -1;
        received += r;
    }
    return 0;
}

// ---------- Local test pattern (tear‑free, uses backBuffer) ----------
static void runLocalTest(void) {
    consoleClear();
    printf("Local Test Pattern\n");
    printf("Divider: %d\n", currentDivider);
    printf("Press START to quit.\n");

    int resDiv = currentDivider;
    int logicW = 256 / resDiv;
    int logicH = 192 / resDiv;

    static u16 testColors[MAX_ITEMS];
    static u16 testAmounts[MAX_ITEMS];
    int idx = 0;
    for (int y = 0; y < logicH; y++) {
        for (int x = 0; x < logicW; x++) {
            if (idx >= MAX_ITEMS) break;
            u8 r = (x * 31) / (logicW - 1);
            u8 g = (y * 31) / (logicH - 1);
            u8 b = 31;
            testColors[idx]  = RGB15(r, g, b);
            testAmounts[idx] = resDiv;
            idx++;
        }
    }

    memset(backBuffer, 0, sizeof(backBuffer));
    decodeScratch(testColors, testAmounts, idx, resDiv, logicW, logicH);

    swiWaitForVBlank();
    swapBuffers();

    while (1) {
        swiWaitForVBlank();
        scanKeys();
        if (keysDown() & KEY_START) break;
    }
}

// ---------- IP editor (auto‑repeat) ----------
static void editIP(void) {
    int octets[4];
    int cursor = 0;
    sscanf(serverIP, "%d.%d.%d.%d", &octets[0], &octets[1], &octets[2], &octets[3]);

    int holdFrames = 0;
    const int HOLD_DELAY = 20;
    const int REPEAT_RATE = 3;

    consoleClear();
    while (1) {
        printf("\x1b[0;0HSet Server IP (D-Pad, A=OK):\n");
        for (int i = 0; i < 4; i++) {
            if (i == cursor) printf("[%3d]", octets[i]);
            else             printf(" %3d ", octets[i]);
            if (i < 3) printf(".");
        }
        printf("\n\nHold UP/DOWN to change\nA: confirm");

        swiWaitForVBlank();
        scanKeys();
        u32 held = keysHeld();
        u32 down = keysDown();

        if (down & KEY_RIGHT && cursor < 3) cursor++;
        if (down & KEY_LEFT  && cursor > 0) cursor--;

        if (held & KEY_UP) {
            if (down & KEY_UP) {
                octets[cursor]++;
                if (octets[cursor] > 255) octets[cursor] = 0;
                holdFrames = 0;
            } else {
                holdFrames++;
                if (holdFrames >= HOLD_DELAY && (holdFrames - HOLD_DELAY) % REPEAT_RATE == 0) {
                    octets[cursor]++;
                    if (octets[cursor] > 255) octets[cursor] = 0;
                }
            }
        } else if (held & KEY_DOWN) {
            if (down & KEY_DOWN) {
                octets[cursor]--;
                if (octets[cursor] < 0) octets[cursor] = 255;
                holdFrames = 0;
            } else {
                holdFrames++;
                if (holdFrames >= HOLD_DELAY && (holdFrames - HOLD_DELAY) % REPEAT_RATE == 0) {
                    octets[cursor]--;
                    if (octets[cursor] < 0) octets[cursor] = 255;
                }
            }
        } else {
            holdFrames = 0;
        }

        if (down & KEY_A) break;
    }

    sprintf(serverIP, "%d.%d.%d.%d", octets[0], octets[1], octets[2], octets[3]);
    consoleClear();
}

// ---------- Auto‑detect resolution divider (with min‑amount guard) ----------
static int detectDivider(const u16* amounts, int numItems) {
    if (numItems == 0) return 4;

    int gcd = amounts[0];
    for (int i = 1; i < numItems; i++) {
        int a = gcd, b = amounts[i];
        while (b != 0) {
            int t = b;
            b = a % b;
            a = t;
        }
        gcd = a;
        if (gcd == 1) break;
    }

    int minAmount = amounts[0];
    for (int i = 1; i < numItems; i++) {
        if (amounts[i] < minAmount) minAmount = amounts[i];
    }

    int best = 1;
    for (int d = 0; d < NUM_DIVIDERS; d++) {
        int div = ALLOWED_DIVIDERS[d];
        if (div > minAmount) break;
        if (gcd % div == 0 && div > best) best = div;
    }
    return best;
}

// ---------- Network stream mode ----------
static void runStreamMode(void) {
    consoleClear();
    printf("Stream mode – searching…\n");
    printf("Press START to quit to menu.\n");

    if (!Wifi_InitDefault(WFC_CONNECT)) {
        printf("WiFi init failed!\n");
        while(1) swiWaitForVBlank();
    }

    // Reset manual override on new stream
    useManualDivider = false;
    manualDivider = 0;

    while (1) {
        // ---- Association ----
        printf("Associating...\n");
        while (1) {
            int status = Wifi_AssocStatus();
            if (status == ASSOCSTATUS_ASSOCIATED) break;
            if (status == ASSOCSTATUS_CANNOTCONNECT) {
                printf("Can't connect to AP. Retrying...\n");
                for (int i = 0; i < 120; i++) {
                    swiWaitForVBlank();
                    scanKeys();
                    if (keysDown() & KEY_START) {
                        printf("Cancelled.\n");
                        return;
                    }
                }
            } else {
                swiWaitForVBlank();
                scanKeys();
                if (keysDown() & KEY_START) {
                    printf("Cancelled.\n");
                    return;
                }
            }
        }

        struct in_addr myIp;
        myIp.s_addr = Wifi_GetIP();
        printf("DS IP: %s\n", inet_ntoa(myIp));

        // ---- Connect to server ----
        int sock = socket(AF_INET, SOCK_STREAM, 0);
        if (sock < 0) {
            printf("Socket error, retrying...\n");
            continue;
        }

        struct sockaddr_in saddr;
        saddr.sin_family = AF_INET;
        saddr.sin_port = htons(SERVER_PORT);
        saddr.sin_addr.s_addr = inet_addr(serverIP);

        printf("Connecting to server...\n");
        while (connect(sock, (struct sockaddr*)&saddr, sizeof(saddr)) < 0) {
            printf("Connect failed, retry in 2s...\n");
            closesocket(sock);
            for (int i = 0; i < 120; i++) {
                swiWaitForVBlank();
                scanKeys();
                if (keysDown() & KEY_START) {
                    printf("Cancelled.\n");
                    return;
                }
            }
            sock = socket(AF_INET, SOCK_STREAM, 0);
            if (sock < 0) { printf("Socket error, back to AP.\n"); break; }
            saddr.sin_family = AF_INET;
            saddr.sin_port = htons(SERVER_PORT);
            saddr.sin_addr.s_addr = inet_addr(serverIP);
        }
        if (sock < 0) continue;

        printf("Connected. Streaming... (START to stop)\n");

        // ---- Stream receive loop ----
        static u16 colors[MAX_ITEMS];
        static u16 amounts[MAX_ITEMS];
        bool userStopped = false;

        while (1) {
            u16 numItems;
            if (recvAll(sock, &numItems, sizeof(numItems)) < 0) {
                printf("Disconnected.\n");
                break;
            }

            // ---- Server‑initiated stop ----
            if (numItems == 0xFFFE) {
                printf("Server requested stop.\n");
                break;
            }

            // ---- Resolution control packet ----
            if (numItems == 0xFFFF) {
                u8 modeByte;
                if (recvAll(sock, &modeByte, 1) < 0) {
                    printf("Disconnected while reading mode.\n");
                    break;
                }
                int mode = modeByte & 0x07;
                if (mode >= 0 && mode <= 6) {
                    manualDivider = 1 << mode;       // 1,2,4,8,16,32,64
                    useManualDivider = true;
                    printf("Resolution set to %d (mode %d)\n", manualDivider, mode);
                } else {
                    printf("Invalid resolution mode %d, ignoring.\n", mode);
                }
                continue;   // read next packet (normal frame)
            }

            // ---- Oversized frame? skip to stay in sync ----
            if (numItems == 0 || numItems > MAX_ITEMS) {
                printf("Bad frame size (%u), skipping...\n", numItems);
                // discard data for this frame
                for (int i = 0; i < numItems && i <= MAX_ITEMS; i++) {
                    u8 dummy; u16 dummy16;
                    recvAll(sock, &dummy, 1);
                    recvAll(sock, &dummy16, sizeof(dummy16));
                }
                continue;
            }

            // ---- Read runs ----
            for (int i = 0; i < numItems; i++) {
                u8 colIdx;
                u16 amt;
                if (recvAll(sock, &colIdx, 1) < 0 ||
                    recvAll(sock, &amt, sizeof(amt)) < 0) {
                    printf("Recv error at run %d\n", i);
                    goto disconnect;
                }
                colors[i]  = palette[colIdx];
                amounts[i] = amt;
            }

            // Determine resolution divider
            int resDiv;
            if (useManualDivider) {
                resDiv = manualDivider;
            } else {
                resDiv = detectDivider(amounts, numItems);
            }

            int logicW = 256 / resDiv;
            int logicH = 192 / resDiv;

            // Decode into back‑buffer
            memset(backBuffer, 0, sizeof(backBuffer));
            decodeScratch(colors, amounts, numItems, resDiv, logicW, logicH);

            // Tear‑free display update
            swiWaitForVBlank();
            swapBuffers();

            printf("\x1b[0;0H");
            printf("Div: %d (%s)   \n", resDiv, useManualDivider ? "manual" : "auto");

            scanKeys();
            if (keysDown() & KEY_START) {
                printf("Stopping stream...\n");
                // Tell the server we're leaving
                u8 stopByte = 0x01;
                send(sock, &stopByte, 1, 0);
                userStopped = true;
                break;
            }
        }

disconnect:
        closesocket(sock);
        if (userStopped) {
            return;   // back to main menu
        }
        printf("Searching for new stream...\n");
    }
}

// ---------- Main ----------
int main(void) {
    videoSetMode(MODE_FB0);
    vramSetBankA(VRAM_A_LCD);
    vramFront = VRAM_A;
    memset(vramFront, 0, 256 * 192 * sizeof(u16));

    videoSetModeSub(MODE_0_2D);
    consoleDemoInit();

    // Build R3G3B2 palette – scaled to 0-31
    for (int i = 0; i < 256; i++) {
        u8 r = (i >> 5) & 7;
        u8 g = (i >> 2) & 7;
        u8 b = i & 3;
        palette[i] = RGB15((r * 31) / 7, (g * 31) / 7, (b * 31) / 3);
    }

    while (1) {
        consoleClear();
        printf("DS Stream Client\n");
        printf("A: Local test\n");
        printf("B: Network stream\n");
        printf("X: Change server IP\n");
        printf("Current IP: %s\n", serverIP);

        swiWaitForVBlank();
        scanKeys();
        u32 keys = keysDown();

        if (keys & KEY_A) {
            runLocalTest();
        }
        if (keys & KEY_B) {
            runStreamMode();
        }
        if (keys & KEY_X) {
            editIP();
        }
    }
    return 0;
}