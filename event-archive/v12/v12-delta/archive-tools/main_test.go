package main

import (
    "bytes"
    "compress/gzip"
    "crypto/sha256"
    "encoding/hex"
    "hash/crc64"
    "os"
    "path/filepath"
    "testing"

    "github.com/mongodb/mongo-tools/common/archive"
    "go.mongodb.org/mongo-driver/bson"
    "go.mongodb.org/mongo-driver/bson/primitive"
)

// Synthetic framing uses the upstream prelude writer and namespace structs, and
// is decoded exclusively by the upstream Parser. No MongoDB server is involved.
func marshal(t *testing.T, value interface{}) []byte {
    t.Helper()
    raw, err := bson.Marshal(value)
    if err != nil { t.Fatal(err) }
    return raw
}

func fixture(t *testing.T, badCRC, missingEOF bool) ([]byte, map[string][]byte) {
    t.Helper()
    var out bytes.Buffer
    prelude := archive.Prelude{Header: &archive.Header{FormatVersion: "0.1", ServerVersion: "7.0.0", ToolVersion: "100.10.0", ConcurrentCollections: 2}}
    names := []string{"a", "nested.name/../x", "empty"}
    for _, name := range names {
        prelude.NamespaceMetadatas = append(prelude.NamespaceMetadatas, &archive.CollectionMetadata{
            Database: "db", Collection: name, Type: "collection",
            Metadata: `{"indexes":[{"v":2,"key":{"_id":1},"name":"_id_"}]}`})
    }
    if err := prelude.Write(&out); err != nil { t.Fatal(err) }
    row1 := marshal(t, bson.D{{Key: "_id", Value: primitive.NewObjectID()}, {Key: "value", Value: int64(7)},
        {Key: "binary", Value: primitive.Binary{Subtype: 128, Data: []byte{0, 255}}},
        {Key: "array", Value: bson.A{1, "x", nil, true}}, {Key: "date", Value: primitive.DateTime(1<<62)}})
    row2 := marshal(t, bson.D{{Key: "_id", Value: bson.D{{Key: "nested", Value: "identity"}}}, {Key: "n", Value: 3}})
    row3 := marshal(t, bson.D{{Key: "_id", Value: "second"}, {Key: "n", Value: 4}})
    raw := map[string][]byte{"a": append(append([]byte{}, row1...), row3...), names[1]: row2, "empty": {}}
    block := func(name string, data []byte, eof bool, crc int64) {
        out.Write(marshal(t, archive.NamespaceHeader{Database: "db", Collection: name, EOF: eof, CRC: crc}))
        out.Write(data)
        out.Write([]byte{255,255,255,255})
    }
    block("a", row1, false, 0)
    block(names[1], row2, false, 0)
    block("a", row3, false, 0) // resumed namespace, genuinely interleaved
    for _, name := range names {
        if missingEOF && name == "a" { continue }
        crc := int64(crc64.Checksum(raw[name], crc64.MakeTable(crc64.ECMA)))
        if badCRC && name == "a" { crc++ }
        block(name, nil, true, crc)
    }
    return out.Bytes(), raw
}

func compressed(t *testing.T, raw []byte, split bool) []byte {
    t.Helper()
    var out bytes.Buffer
    portions := [][]byte{raw}
    if split { portions = [][]byte{raw[:7], raw[7:]} } // split inside BSON, unrelated to namespaces
    for _, part := range portions {
        stream := gzip.NewWriter(&out)
        if _, err := stream.Write(part); err != nil { t.Fatal(err) }
        if err := stream.Close(); err != nil { t.Fatal(err) }
    }
    return out.Bytes()
}

func TestOfficialParserInterleavingGzipMembersBSONAndEmptyCollection(t *testing.T) {
    raw, expected := fixture(t, false, false)
    for _, split := range []bool{false,true} {
        input := compressed(t, raw, split)
        directory := t.TempDir()
        inv, err := extract(bytes.NewReader(input), directory, "db", 3)
        if err != nil { t.Fatal(err) }
        digest := sha256.Sum256(input)
        if inv.InputSHA256 != hex.EncodeToString(digest[:]) { t.Fatal("input SHA256 mismatch") }
        for _, e := range inv.Collections {
            data, err := os.ReadFile(filepath.Join(directory, e.File))
            if err != nil || !bytes.Equal(data, expected[e.Collection]) { t.Fatal("BSON bytes not preserved", err) }
            digest := sha256.Sum256(data)
            if e.SHA256 != hex.EncodeToString(digest[:]) { t.Fatal("BSON SHA256 mismatch") }
        }
    }
}

func TestRejectCorruptionIncompleteNamespaceAndWrongDatabase(t *testing.T) {
    for _, flags := range [][2]bool{{true,false},{false,true}} {
        raw, _ := fixture(t, flags[0], flags[1])
        if _, err := extract(bytes.NewReader(compressed(t, raw, false)), t.TempDir(), "db", 3); err == nil {
            t.Fatal("accepted bad CRC or missing EOF")
        }
    }
    raw, _ := fixture(t, false, false)
    input := compressed(t, raw, false)
    cases := [][]byte{input[:len(input)-5], append(append([]byte{}, input...), []byte("junk")...), compressed(t, raw[:len(raw)-2], false), compressed(t, []byte("ordinary BSON is not an archive"), false)}
    for _, bad := range cases {
        if _, err := extract(bytes.NewReader(bad), t.TempDir(), "db", 3); err == nil { t.Fatal("accepted corrupt input") }
    }
    if _, err := extract(bytes.NewReader(input), t.TempDir(), "foreign", 3); err == nil { t.Fatal("accepted foreign database") }
    if _, err := extract(bytes.NewReader(input), t.TempDir(), "db", 64); err == nil { t.Fatal("accepted wrong collection count") }
}
