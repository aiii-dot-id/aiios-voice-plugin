//go:build !wasm_unknown

package aiiosdk

import "errors"

type faultWriter struct{}

func (faultWriter) Write([]byte) (int, error) {
	return 0, errors.New("output pipe failed")
}
