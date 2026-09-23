#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

using std::size_t;
using std::string;

// Strips leading zeros (keeping at least one digit) and compares the
// resulting digit string against maxStr (which has no leading zeros).
// Returns true if the value exceeds maxStr.
static bool exceedsMax(const string &digits, const string &maxStr, string &strippedOut) {
    size_t start = 0;
    while (start + 1 < digits.size() && digits[start] == '0') start++;
    strippedOut = digits.substr(start);
    if (strippedOut.size() > maxStr.size()) return true;
    if (strippedOut.size() < maxStr.size()) return false;
    return strippedOut > maxStr;
}

static uint64_t toU64(const string &digits) {
    uint64_t v = 0;
    for (char c : digits) v = v * 10 + static_cast<uint64_t>(c - '0');
    return v;
}

// Checks a plain decimal field (id or qty). Returns true on error.
static bool checkIntField(const string &field, int startCol, const string &maxStr,
                           const char *&kind, int &col, uint64_t &value) {
    if (field.empty()) {
        kind = "empty";
        col = startCol;
        return true;
    }
    for (size_t i = 0; i < field.size(); ++i) {
        if (field[i] < '0' || field[i] > '9') {
            kind = "digit";
            col = startCol + static_cast<int>(i);
            return true;
        }
    }
    string stripped;
    if (exceedsMax(field, maxStr, stripped)) {
        kind = "range";
        col = startCol;
        return true;
    }
    value = toU64(stripped);
    return false;
}

// Checks the price field. Returns true on error.
static bool checkPriceField(const string &field, int startCol, const char *&kind, int &col,
                             uint64_t &value) {
    if (field.empty()) {
        kind = "empty";
        col = startCol;
        return true;
    }
    for (size_t i = 0; i < field.size(); ++i) {
        char c = field[i];
        if (!((c >= '0' && c <= '9') || c == '.')) {
            kind = "digit";
            col = startCol + static_cast<int>(i);
            return true;
        }
    }
    size_t ndots = 0;
    size_t dotPos = string::npos;
    for (size_t i = 0; i < field.size(); ++i) {
        if (field[i] == '.') {
            ndots++;
            if (dotPos == string::npos) dotPos = i;
        }
    }
    size_t beforeLen = (dotPos == string::npos) ? 0 : dotPos;
    size_t afterLen = (dotPos == string::npos) ? 0 : field.size() - dotPos - 1;
    if (ndots != 1 || beforeLen < 1 || afterLen != 2) {
        kind = "format";
        col = startCol;
        return true;
    }
    string before = field.substr(0, dotPos);
    string after = field.substr(dotPos + 1);
    string combined = before + after;
    static const string maxCents = "9223372036854775807";
    string stripped;
    if (exceedsMax(combined, maxCents, stripped)) {
        kind = "range";
        col = startCol;
        return true;
    }
    value = toU64(stripped);
    return false;
}

static const string MAX_ID = "4294967295";
static const string MAX_QTY = "65535";

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    string data((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());

    string out;
    out.reserve(data.size());

    size_t start = 0;
    long lineNo = 0;
    size_t n = data.size();
    while (start < n) {
        size_t nl = data.find('\n', start);
        string line;
        if (nl == string::npos) {
            line = data.substr(start);
            start = n;
        } else {
            line = data.substr(start, nl - start);
            start = nl + 1;
        }
        lineNo++;

        std::vector<size_t> commas;
        for (size_t i = 0; i < line.size(); ++i) {
            if (line[i] == ',') commas.push_back(i);
        }

        if (commas.size() < 2) {
            out += "err " + std::to_string(lineNo) + " " + std::to_string(line.size() + 1) +
                   " missing\n";
            continue;
        }
        if (commas.size() > 2) {
            out += "err " + std::to_string(lineNo) + " " + std::to_string(commas[2] + 1) +
                   " extra\n";
            continue;
        }

        string idField = line.substr(0, commas[0]);
        string qtyField = line.substr(commas[0] + 1, commas[1] - commas[0] - 1);
        string priceField = line.substr(commas[1] + 1);

        int idStart = 1;
        int qtyStart = static_cast<int>(commas[0]) + 2;
        int priceStart = static_cast<int>(commas[1]) + 2;

        const char *kind = nullptr;
        int col = 0;
        uint64_t idVal = 0, qtyVal = 0, priceVal = 0;

        bool err = checkIntField(idField, idStart, MAX_ID, kind, col, idVal);
        if (!err) err = checkIntField(qtyField, qtyStart, MAX_QTY, kind, col, qtyVal);
        if (!err) err = checkPriceField(priceField, priceStart, kind, col, priceVal);

        if (err) {
            out += "err " + std::to_string(lineNo) + " " + std::to_string(col) + " " + kind + "\n";
        } else {
            out += "ok " + std::to_string(idVal) + " " + std::to_string(qtyVal) + " " +
                   std::to_string(priceVal) + "\n";
        }
    }

    std::cout << out;
    return 0;
}
