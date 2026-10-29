package main

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"go/parser"
	"go/printer"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func main() {
	for _, dir := range os.Args[1:] {
		names, _ := filepath.Glob(filepath.Join(dir, "*.go"))
		sort.Strings(names)
		h := sha256.New()
		n := 0
		for _, name := range names {
			if strings.HasSuffix(name, "_test.go") {
				continue
			}
			fset := token.NewFileSet()
			f, err := parser.ParseFile(fset, name, nil, parser.SkipObjectResolution)
			if err != nil {
				panic(err)
			}
			f.Comments = nil
			var b bytes.Buffer
			if err := (&printer.Config{Mode: printer.UseSpaces | printer.TabIndent, Tabwidth: 8}).Fprint(&b, fset, f); err != nil {
				panic(err)
			}
			fmt.Fprintf(h, "%s\n%d\n", filepath.Base(name), b.Len())
			h.Write(b.Bytes())
			n++
		}
		fmt.Printf("%x  %d files  %s\n", h.Sum(nil), n, dir)
	}
}
