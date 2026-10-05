package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"
	"text/template"
)

func main() {
	var input struct {
		Template string                 `json:"template"`
		XR       map[string]interface{} `json:"xr"`
		// Observed composed resources keyed by resource name, each shaped
		// like the function request: {"resource": {...}, "connectionDetails": {...}}.
		Observed map[string]interface{} `json:"observed"`
		Desired  map[string]interface{} `json:"desired"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		panic(err)
	}
	funcs := template.FuncMap{
		"setResourceNameAnnotation": func(name string) string {
			return "gotemplating.fn.crossplane.io/composition-resource-name: " + name
		},
		"getComposedResource": func(_ interface{}, name string) interface{} {
			if observed, ok := input.Observed[name].(map[string]interface{}); ok {
				return observed["resource"]
			}
			return nil
		},
		"dict": func() map[string]interface{} { return map[string]interface{}{} },
		"get": func(values map[string]interface{}, key string) interface{} {
			if value, ok := values[key]; ok {
				return value
			}
			return ""
		},
		"default": func(fallback interface{}, value interface{}) interface{} {
			if value == nil || value == "" || value == false {
				return fallback
			}
			if m, ok := value.(map[string]interface{}); ok && len(m) == 0 {
				return fallback
			}
			return value
		},
		"list": func(values ...interface{}) []interface{} { return values },
		"append": func(values []interface{}, value interface{}) []interface{} {
			return append(values, value)
		},
		"join": func(separator string, values []interface{}) string {
			parts := make([]string, len(values))
			for i, value := range values {
				parts[i] = fmt.Sprint(value)
			}
			return strings.Join(parts, separator)
		},
		"set": func(values map[string]interface{}, key string, value interface{}) map[string]interface{} {
			values[key] = value
			return values
		},
		"deepCopy": func(value interface{}) interface{} {
			encoded, err := json.Marshal(value)
			if err != nil {
				panic(err)
			}
			var result interface{}
			if err := json.Unmarshal(encoded, &result); err != nil {
				panic(err)
			}
			return result
		},
		"toJson": func(value interface{}) string {
			encoded, err := json.Marshal(value)
			if err != nil {
				panic(err)
			}
			return string(encoded)
		},
		"b64dec": func(value interface{}) string {
			encoded, _ := value.(string)
			decoded, err := base64.StdEncoding.DecodeString(encoded)
			if err != nil {
				panic(err)
			}
			return string(decoded)
		},
		"trimSuffix": func(suffix, value string) string { return strings.TrimSuffix(value, suffix) },
		"trimPrefix": func(prefix, value string) string { return strings.TrimPrefix(value, prefix) },
		"regexFind":  func(pattern, value string) string { return regexp.MustCompile(pattern).FindString(value) },
		"quote": func(value interface{}) string {
			encoded, err := json.Marshal(value)
			if err != nil {
				panic(err)
			}
			return string(encoded)
		},
		"toYaml": func(values map[string]interface{}) string {
			keys := make([]string, 0, len(values))
			for key := range values {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			var lines []string
			for _, key := range keys {
				value, err := json.Marshal(values[key])
				if err != nil {
					panic(err)
				}
				lines = append(lines, fmt.Sprintf("%s: %s", key, value))
			}
			return strings.Join(lines, "\n")
		},
		"nindent": func(spaces int, value string) string {
			return "\n" + strings.Repeat(" ", spaces) + strings.ReplaceAll(value, "\n", "\n"+strings.Repeat(" ", spaces))
		},
	}
	tmpl, err := template.New("composition").Funcs(funcs).Parse(input.Template)
	if err != nil {
		panic(err)
	}
	data := map[string]interface{}{
		"desired": map[string]interface{}{"resources": input.Desired},
		"observed": map[string]interface{}{
			"composite": map[string]interface{}{"resource": input.XR},
			"resources": input.Observed,
		},
	}
	if err := tmpl.Execute(os.Stdout, data); err != nil {
		panic(err)
	}
}
