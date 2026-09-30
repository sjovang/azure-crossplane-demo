package main

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"text/template"
)

func main() {
	var input struct {
		Template string                 `json:"template"`
		XR       map[string]interface{} `json:"xr"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		panic(err)
	}
	funcs := template.FuncMap{
		"setResourceNameAnnotation": func(name string) string {
			return "gotemplating.fn.crossplane.io/composition-resource-name: " + name
		},
		"getComposedResource": func(_ interface{}, _ string) interface{} { return nil },
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
		"observed": map[string]interface{}{
			"composite": map[string]interface{}{"resource": input.XR},
		},
	}
	if err := tmpl.Execute(os.Stdout, data); err != nil {
		panic(err)
	}
}
