// Command inspect_carrier_binding reads the compiled Go string variable from
// a desktop carrier. Searching binary bytes for a digest is insufficient: an
// attacker could append the expected text to an executable with no binding.
package main

import (
	"debug/elf"
	"debug/macho"
	"debug/pe"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

const bindingSymbol = "main.packagedRuntimeSHA"

type symbol struct {
	address uint64
	size    uint64
}

func symbols(path string) (symbol, symbol, error) {
	tool := filepath.Join(runtime.GOROOT(), "bin", "go")
	raw, err := exec.Command(tool, "tool", "nm", "-size", path).Output()
	if err != nil {
		return symbol{}, symbol{}, fmt.Errorf("Go symbol table unavailable: %w", err)
	}
	var binding, backing symbol
	seenBinding, seenBacking := false, false
	for _, line := range strings.Split(string(raw), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 4 {
			continue
		}
		if fields[3] != bindingSymbol && fields[3] != bindingSymbol+".str" {
			continue
		}
		address, e1 := strconv.ParseUint(fields[0], 16, 64)
		size, e2 := strconv.ParseUint(fields[1], 10, 64)
		if e1 != nil || e2 != nil {
			return symbol{}, symbol{}, errors.New("invalid binding symbol")
		}
		if fields[3] == bindingSymbol {
			if seenBinding || fields[2] != "D" {
				return symbol{}, symbol{}, errors.New("duplicate or non-data binding symbol")
			}
			binding, seenBinding = symbol{address, size}, true
		} else {
			if seenBacking || fields[2] != "R" {
				return symbol{}, symbol{}, errors.New("duplicate or non-readonly backing symbol")
			}
			backing, seenBacking = symbol{address, size}, true
		}
	}
	if !seenBinding || !seenBacking || binding.size != 16 || backing.size < 64 {
		return symbol{}, symbol{}, errors.New("compiled binding symbols missing or malformed")
	}
	return binding, backing, nil
}

func fileOffset(path string, address, size uint64) (uint64, error) {
	if f, err := elf.Open(path); err == nil {
		defer f.Close()
		for _, p := range f.Progs {
			if p.Type == elf.PT_LOAD && address >= p.Vaddr && size <= p.Filesz && address-p.Vaddr <= p.Filesz-size {
				return p.Off + address - p.Vaddr, nil
			}
		}
		return 0, errors.New("ELF binding address is not file backed")
	}
	if f, err := macho.Open(path); err == nil {
		defer f.Close()
		for _, s := range f.Sections {
			if address >= s.Addr && size <= s.Size && address-s.Addr <= s.Size-size && s.Offset != 0 {
				return uint64(s.Offset) + address - s.Addr, nil
			}
		}
		return 0, errors.New("Mach-O binding address is not file backed")
	}
	if f, err := pe.Open(path); err == nil {
		defer f.Close()
		var base uint64
		switch h := f.OptionalHeader.(type) {
		case *pe.OptionalHeader64:
			base = h.ImageBase
		case *pe.OptionalHeader32:
			base = uint64(h.ImageBase)
		default:
			return 0, errors.New("PE image base missing")
		}
		if address < base {
			return 0, errors.New("PE binding precedes image")
		}
		rva := address - base
		for _, s := range f.Sections {
			start, fileSize := uint64(s.VirtualAddress), uint64(s.Size)
			if rva >= start && size <= fileSize && rva-start <= fileSize-size {
				return uint64(s.Offset) + rva - start, nil
			}
		}
		return 0, errors.New("PE binding address is not file backed")
	}
	return 0, errors.New("unsupported carrier executable")
}

func readAddress(f *os.File, path string, address, size uint64) ([]byte, error) {
	offset, err := fileOffset(path, address, size)
	if err != nil {
		return nil, err
	}
	if size > 64 {
		return nil, errors.New("binding read too large")
	}
	value := make([]byte, size)
	if _, err := f.ReadAt(value, int64(offset)); err != nil {
		return nil, err
	}
	return value, nil
}

func inspect(path, expected string) error {
	if len(expected) != 64 || strings.Trim(expected, "0123456789abcdef") != "" {
		return errors.New("expected runtime digest must be lowercase SHA-256")
	}
	binding, backing, err := symbols(path)
	if err != nil {
		return err
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	header, err := readAddress(f, path, binding.address, binding.size)
	if err != nil {
		return err
	}
	pointer, length := binary.LittleEndian.Uint64(header[:8]), binary.LittleEndian.Uint64(header[8:])
	if pointer != backing.address || length != 64 {
		return errors.New("compiled runtime string does not point to its bound data")
	}
	value, err := readAddress(f, path, pointer, length)
	if err != nil {
		return err
	}
	if string(value) != expected {
		return errors.New("compiled runtime digest differs")
	}
	return nil
}

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: inspect_carrier_binding executable sha256")
		os.Exit(2)
	}
	if err := inspect(os.Args[1], os.Args[2]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"passed": true, "runtime_manifest_sha256": os.Args[2]})
}
