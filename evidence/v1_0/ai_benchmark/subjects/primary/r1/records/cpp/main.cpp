#include <cstdio>
#include <string>
#include <vector>
#include <iostream>

static bool isDigit(char c) { return c >= '0' && c <= '9'; }

static std::string stripLeadingZeros(const std::string &s) {
    size_t i = 0;
    while (i + 1 < s.size() && s[i] == '0') i++;
    return s.substr(i);
}

// true if strippedDigits (no leading zeros) <= maxStr numerically
static bool leMax(const std::string &strippedDigits, const std::string &maxStr) {
    if (strippedDigits.size() != maxStr.size())
        return strippedDigits.size() < maxStr.size();
    return strippedDigits <= maxStr;
}

struct FieldResult {
    bool ok;
    std::string kind;   // if !ok
    long col;            // if !ok, absolute column in line
    std::string value;  // if ok, stripped decimal digits
};

// kind: 0 = id/qty (digits only), 1 = price (digits + '.')
static FieldResult checkField(const std::string &line, size_t fieldStart, size_t fieldLen,
                               int kind, const std::string &maxStr) {
    long startCol = (long)fieldStart + 1;
    if (fieldLen == 0) {
        return {false, "empty", startCol, ""};
    }
    std::string f = line.substr(fieldStart, fieldLen);

    // digit check
    for (size_t i = 0; i < f.size(); i++) {
        char c = f[i];
        bool allowed = isDigit(c) || (kind == 1 && c == '.');
        if (!allowed) {
            return {false, "digit", startCol + (long)i, ""};
        }
    }

    std::string digitsForRange;
    if (kind == 1) {
        size_t dotCount = 0;
        size_t dotPos = std::string::npos;
        for (size_t i = 0; i < f.size(); i++) {
            if (f[i] == '.') {
                dotCount++;
                if (dotPos == std::string::npos) dotPos = i;
            }
        }
        bool shapeOk = false;
        if (dotCount == 1) {
            size_t intLen = dotPos;
            size_t fracLen = f.size() - dotPos - 1;
            if (intLen > 0 && fracLen == 2) shapeOk = true;
        }
        if (!shapeOk) {
            return {false, "format", startCol, ""};
        }
        std::string intPart = f.substr(0, dotPos);
        std::string fracPart = f.substr(dotPos + 1);
        digitsForRange = intPart + fracPart;
    } else {
        digitsForRange = f;
    }

    std::string stripped = stripLeadingZeros(digitsForRange);
    if (!leMax(stripped, maxStr)) {
        return {false, "range", startCol, ""};
    }
    return {true, "", 0, stripped};
}

int main() {
    std::ios::sync_with_stdio(false);
    std::string data((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());

    std::vector<std::pair<size_t, size_t>> lines; // start, length
    size_t start = 0;
    while (start <= data.size()) {
        size_t pos = data.find('\n', start);
        if (pos == std::string::npos) {
            if (start < data.size()) {
                lines.push_back({start, data.size() - start});
            }
            break;
        } else {
            lines.push_back({start, pos - start});
            start = pos + 1;
        }
    }

    const std::string idMax = "4294967295";
    const std::string qtyMax = "65535";
    const std::string priceMax = "9223372036854775807";

    std::string out;
    out.reserve(data.size() + lines.size() * 8);

    for (size_t lineNo = 0; lineNo < lines.size(); lineNo++) {
        size_t lStart = lines[lineNo].first;
        size_t lLen = lines[lineNo].second;
        std::string line = data.substr(lStart, lLen);

        std::vector<size_t> commas;
        for (size_t i = 0; i < line.size(); i++) {
            if (line[i] == ',') commas.push_back(i);
        }

        if (commas.size() < 2) {
            out += "err " + std::to_string(lineNo + 1) + " " + std::to_string((long)line.size() + 1) + " missing\n";
            continue;
        }
        if (commas.size() > 2) {
            out += "err " + std::to_string(lineNo + 1) + " " + std::to_string((long)commas[2] + 1) + " extra\n";
            continue;
        }

        size_t c0 = commas[0], c1 = commas[1];
        size_t f0Start = 0, f0Len = c0;
        size_t f1Start = c0 + 1, f1Len = c1 - c0 - 1;
        size_t f2Start = c1 + 1, f2Len = line.size() - c1 - 1;

        FieldResult idRes = checkField(line, f0Start, f0Len, 0, idMax);
        if (!idRes.ok) {
            out += "err " + std::to_string(lineNo + 1) + " " + std::to_string(idRes.col) + " " + idRes.kind + "\n";
            continue;
        }
        FieldResult qtyRes = checkField(line, f1Start, f1Len, 0, qtyMax);
        if (!qtyRes.ok) {
            out += "err " + std::to_string(lineNo + 1) + " " + std::to_string(qtyRes.col) + " " + qtyRes.kind + "\n";
            continue;
        }
        FieldResult priceRes = checkField(line, f2Start, f2Len, 1, priceMax);
        if (!priceRes.ok) {
            out += "err " + std::to_string(lineNo + 1) + " " + std::to_string(priceRes.col) + " " + priceRes.kind + "\n";
            continue;
        }

        out += "ok " + idRes.value + " " + qtyRes.value + " " + priceRes.value + "\n";
    }

    fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}
