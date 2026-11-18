// V11 adapter around MongoDB Database Tools' official archive parser.
// This program has no MongoDB client, connection flags, or server write path.
package main

import (
    "compress/gzip"
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "errors"
    "flag"
    "fmt"
    "hash"
    "hash/crc64"
    "io"
    "os"
    "path/filepath"

    "github.com/mongodb/mongo-tools/common/archive"
    "go.mongodb.org/mongo-driver/bson"
)

const toolsRevision = "6d4f001be3fcf673de04d20176e90ee02ef233a9"
const maxPrelude = 16 * 1024 * 1024

type entry struct {
    Database string `json:"database"`
    Collection string `json:"collection"`
    Metadata string `json:"metadata"`
    Type string `json:"type"`
    DeclaredSize int `json:"declared_size"`
    File string `json:"file"`
    Count int64 `json:"count"`
    Bytes int64 `json:"bytes"`
    SHA256 string `json:"sha256"`
    CRC64 string `json:"crc64"`
    crc hash.Hash64
    digest hash.Hash
    closed bool
}

type inventory struct {
    Protocol int `json:"protocol"`
    ToolsRevision string `json:"tools_revision"`
    ArchiveVersion string `json:"archive_version"`
    ConcurrentCollections int32 `json:"concurrent_collections"`
    ServerVersion string `json:"server_version"`
    ToolVersion string `json:"tool_version"`
    InputSHA256 string `json:"input_sha256"`
    Collections []*entry `json:"collections"`
}

type consumer struct {
    directory string
    entries map[string]*entry
    current *entry
    output *os.File
}

func (c *consumer) closeOutput() error {
    if c.output == nil { return nil }
    err := c.output.Close()
    c.output = nil
    return err
}

func (c *consumer) HeaderBSON(raw []byte) error {
    if err := c.closeOutput(); err != nil { return err }
    var header archive.NamespaceHeader
    if err := bson.Unmarshal(raw, &header); err != nil { return err }
    // Fail closed for unknown header fields; the pinned upstream struct ignores them.
    var fields bson.M
    if err := bson.Unmarshal(raw, &fields); err != nil { return err }
    for key := range fields {
        if key != "db" && key != "collection" && key != "EOF" && key != "CRC" {
            return fmt.Errorf("unsupported namespace header field %q", key)
        }
    }
    e, ok := c.entries[header.Database + "." + header.Collection]
    if !ok || e.closed { return errors.New("unknown or already closed namespace") }
    c.current = nil
    if header.EOF {
        if int64(e.crc.Sum64()) != header.CRC { return fmt.Errorf("CRC mismatch: %s", header.Collection) }
        e.CRC64 = fmt.Sprintf("%016x", e.crc.Sum64())
        e.SHA256 = hex.EncodeToString(e.digest.Sum(nil))
        e.closed = true
        return nil
    }
    if header.CRC != 0 { return errors.New("CRC on non-EOF block") }
    out, err := os.OpenFile(filepath.Join(c.directory, e.File), os.O_WRONLY|os.O_APPEND, 0600)
    if err != nil { return err }
    c.current, c.output = e, out
    return nil
}

func (c *consumer) BodyBSON(raw []byte) error {
    if c.current == nil || c.output == nil { return errors.New("data in EOF block") }
    if err := bson.Raw(raw).Validate(); err != nil { return err }
    if _, err := c.output.Write(raw); err != nil { return err }
    c.current.crc.Write(raw)
    c.current.digest.Write(raw)
    c.current.Count++
    c.current.Bytes += int64(len(raw))
    return nil
}

func (c *consumer) End() error {
    if err := c.closeOutput(); err != nil { return err }
    for name, e := range c.entries {
        if !e.closed { return fmt.Errorf("missing namespace EOF: %s", name) }
    }
    return nil
}

func extract(input io.Reader, directory, database string, expected int) (*inventory, error) {
    digest := sha256.New()
    compressed := io.TeeReader(input, digest)
    stream, err := gzip.NewReader(compressed)
    if err != nil { return nil, err }
    defer stream.Close()
    // gzip multistream is intentional; gzip member boundaries are not namespaces.
    prelude := &archive.Prelude{}
    limited := &io.LimitedReader{R: stream, N: maxPrelude}
    if err := prelude.Read(limited); err != nil { return nil, err }
    if limited.N == 0 { return nil, errors.New("prelude exceeds limit") }
    if prelude.Header == nil || prelude.Header.FormatVersion != "0.1" {
        return nil, errors.New("unsupported archive format")
    }
    if len(prelude.NamespaceMetadatas) != expected || expected < 1 || expected > 4096 {
        return nil, fmt.Errorf("unexpected collection count: expected %d, found %d", expected, len(prelude.NamespaceMetadatas))
    }
    result := &inventory{Protocol: 1, ToolsRevision: toolsRevision,
        ArchiveVersion: prelude.Header.FormatVersion, ConcurrentCollections: prelude.Header.ConcurrentCollections, ServerVersion: prelude.Header.ServerVersion,
        ToolVersion: prelude.Header.ToolVersion}
    c := &consumer{directory: directory, entries: make(map[string]*entry)}
    defer c.closeOutput()
    for i, cm := range prelude.NamespaceMetadatas {
        if cm.Database != database || cm.Collection == "" || cm.Metadata == "" ||
            (cm.Type != "" && cm.Type != "collection") {
            return nil, errors.New("foreign database, missing metadata, or unsupported collection type (including oplog/views)")
        }
        name := cm.Database + "." + cm.Collection
        if _, exists := c.entries[name]; exists { return nil, errors.New("duplicate namespace") }
        e := &entry{Database: cm.Database, Collection: cm.Collection, Metadata: cm.Metadata,
            Type: cm.Type, DeclaredSize: cm.Size, File: fmt.Sprintf("c%06d.bson", i),
            crc: crc64.New(crc64.MakeTable(crc64.ECMA)), digest: sha256.New()}
        // Ordinal names prevent collection names from becoming filesystem paths.
        file, err := os.OpenFile(filepath.Join(directory, e.File), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
        if err != nil { return nil, err }
        if err := file.Close(); err != nil { return nil, err }
        c.entries[name] = e
        result.Collections = append(result.Collections, e)
    }
    parser := archive.Parser{In: stream}
    if err := parser.ReadAllBlocks(c); err != nil { return nil, err }
    // ReadAllBlocks reads to gzip EOF, validating every gzip trailer and namespace EOF.
    result.InputSHA256 = hex.EncodeToString(digest.Sum(nil))
    return result, nil
}

func run() error {
    input := flag.String("input", "", "Read-only mongodump --archive --gzip input")
    out := flag.String("out", "", "Existing empty V11 scratch directory")
    database := flag.String("database", "", "Only database allowed in archive")
    expected := flag.Int("expected-collections", 64, "Exact namespace count")
    flag.Parse()
    if *input == "" || *out == "" || *database == "" { return errors.New("input, out, database required") }
    entries, err := os.ReadDir(*out)
    if err != nil || len(entries) != 0 { return errors.New("output must be an existing empty directory") }
    file, err := os.Open(*input)
    if err != nil { return err }
    defer file.Close()
    before, err := file.Stat()
    if err != nil || !before.Mode().IsRegular() { return errors.New("input must be a regular file") }
    result, err := extract(file, *out, *database, *expected)
    if err != nil { return err }
    after, err := file.Stat()
    if err != nil || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
        return errors.New("input changed during extraction")
    }
    // This success inventory is emitted only after all integrity checks have passed.
    output, err := os.OpenFile(filepath.Join(*out, "inventory.json"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
    if err != nil { return err }
    err = json.NewEncoder(output).Encode(result)
    closeErr := output.Close()
    if err != nil { return err }
    return closeErr
}

func main() {
    if err := run(); err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(1) }
}
