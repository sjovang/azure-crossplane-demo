// A deliberately small template harness; real-function checks are documented
// separately. No fake Azure or Kubernetes reconciliation happens here.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"text/template"
)

func main() {
	var input struct {
		Template string                 `json:"template"`
		XR       map[string]interface{} `json:"xr"`
		Observed map[string]interface{} `json:"observed"`
		Extra    map[string]interface{} `json:"extra"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		panic(err)
	}
	funcs := template.FuncMap{
		"dict": func() map[string]interface{} { return map[string]interface{}{} },
		"list": func(values ...interface{}) []interface{} { return values },
		"append": func(values []interface{}, value interface{}) []interface{} {
			return append(values, value)
		},
		"get": func(values map[string]interface{}, key string) interface{} { return values[key] },
		"set": func(values map[string]interface{}, key string, value interface{}) map[string]interface{} {
			values[key] = value
			return values
		},
		"default": func(fallback interface{}, value interface{}) interface{} {
			if value == nil || reflect.ValueOf(value).IsZero() {
				return fallback
			}
			v := reflect.ValueOf(value)
			if (v.Kind() == reflect.Map || v.Kind() == reflect.Slice) && v.Len() == 0 {
				return fallback
			}
			return value
		},
		"deepCopy": func(value interface{}) interface{} {
			b, _ := json.Marshal(value)
			var out interface{}
			if err := json.Unmarshal(b, &out); err != nil {
				panic(err)
			}
			return out
		},
		"toJson": func(value interface{}) string {
			b, err := json.Marshal(value)
			if err != nil {
				panic(err)
			}
			return string(b)
		},
		"quote": func(value interface{}) string {
			// Sprig quote stringifies booleans; json.Marshal alone does not.
			b, _ := json.Marshal(fmt.Sprint(value))
			return string(b)
		},
		"toYaml": func(value interface{}) string {
			b, err := json.Marshal(value)
			if err != nil {
				panic(err)
			}
			return string(b)
		},
		"getComposedResource": func(_ interface{}, name string) interface{} {
			if value, ok := input.Observed[name].(map[string]interface{}); ok {
				return value["resource"]
			}
			return nil
		},
		"fail":       func(message string) (string, error) { return "", errors.New(message) },
		"trimSuffix": func(suffix, value string) string { return strings.TrimSuffix(value, suffix) },
	}
	t, err := template.New("management").Funcs(funcs).Parse(input.Template)
	if err != nil {
		panic(err)
	}
	data := map[string]interface{}{
		"observed": map[string]interface{}{
			"composite": map[string]interface{}{"resource": input.XR},
			"resources": input.Observed,
		},
		"extraResources": input.Extra,
	}
	if err := t.Execute(os.Stdout, data); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
